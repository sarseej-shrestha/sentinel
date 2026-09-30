"""Reproducible CUDA QLoRA adapter training; never silently substitute CPU training."""
import argparse
import json
from pathlib import Path
from sentinel.config import PLANNER_MODEL, SEED


def encode_example(tokenizer, example, max_length=4096):
    messages = example["messages"]
    prompt = tokenizer.apply_chat_template(messages[:-1], tokenize=True, add_generation_prompt=True)
    full = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False)
    if len(full) > max_length:
        raise ValueError("Training example exceeds maximum length; increase it rather than truncating the target JSON")
    if full[:len(prompt)] != prompt:
        raise ValueError("Chat template prefix mismatch; cannot reliably mask prompt tokens")
    return {"input_ids": full, "attention_mask": [1]*len(full), "labels": [-100]*len(prompt) + full[len(prompt):]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/training"))
    parser.add_argument("--output", type=Path, default=Path("models/sentinel-qlora"))
    parser.add_argument("--epochs", type=float, default=1)
    parser.add_argument("--max-length", type=int, default=4096)
    args = parser.parse_args()
    try:
        import torch
    except ImportError:
        raise SystemExit("Training not run: install the models extra on a CUDA host.")
    if not torch.cuda.is_available():
        raise SystemExit("Training not run: this QLoRA recipe requires a CUDA GPU.")
    from datasets import load_dataset
    from transformers import AutoTokenizer, AutoModelForCausalLM, BitsAndBytesConfig, DataCollatorForSeq2Seq, Trainer, TrainingArguments, set_seed
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    set_seed(SEED)
    data = load_dataset("json", data_files={split: str(args.data / f"{split}.jsonl") for split in ("train", "validation")})
    if set(data["train"]["entity_group"]) & set(data["validation"]["entity_group"]):
        raise ValueError("Entity leakage between training and validation")
    tokenizer = AutoTokenizer.from_pretrained(PLANNER_MODEL)
    tokenizer.pad_token = tokenizer.eos_token
    encoded = data.map(lambda row: encode_example(tokenizer, row, args.max_length), remove_columns=data["train"].column_names)
    dtype = torch.bfloat16 if torch.cuda.is_bf16_supported() else torch.float16
    quantization = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True, bnb_4bit_compute_dtype=dtype)
    model = AutoModelForCausalLM.from_pretrained(PLANNER_MODEL, quantization_config=quantization, device_map={"": torch.cuda.current_device()})
    model = prepare_model_for_kbit_training(model)
    model = get_peft_model(model, LoraConfig(r=16, lora_alpha=32, target_modules="all-linear", lora_dropout=0.05, bias="none", task_type="CAUSAL_LM"))
    model.config.use_cache = False
    training = TrainingArguments(output_dir=str(args.output), seed=SEED, data_seed=SEED, num_train_epochs=args.epochs,
        per_device_train_batch_size=1, per_device_eval_batch_size=1, gradient_accumulation_steps=8,
        learning_rate=2e-4, bf16=dtype == torch.bfloat16, fp16=dtype == torch.float16,
        gradient_checkpointing=True, eval_strategy="epoch", save_strategy="epoch", logging_steps=10, report_to="none")
    trainer = Trainer(model=model, args=training, train_dataset=encoded["train"], eval_dataset=encoded["validation"],
                      data_collator=DataCollatorForSeq2Seq(tokenizer, padding=True, label_pad_token_id=-100))
    result = trainer.train()
    metrics = {"training": result.metrics, "validation": trainer.evaluate(), "base_model": PLANNER_MODEL, "seed": SEED,
               "limitation": "Template-generated synthetic examples; validation loss is not SQL execution accuracy."}
    trainer.save_model(str(args.output))
    tokenizer.save_pretrained(args.output)
    (args.output / "measured_training_metrics.json").write_text(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
