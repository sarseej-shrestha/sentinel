"""JSON-only planner contract; offline rules are labeled separately from Qwen."""
import json
import re
from jsonschema import Draft202012Validator
from sentinel.config import AS_OF, PLANNER_MODEL
from sentinel.nlq.schema import SCHEMA, PLAN_SCHEMA


def validate_plan(value):
    if isinstance(value, str):
        value = json.loads(value)
    Draft202012Validator(PLAN_SCHEMA).validate(value)
    if value["intent"] in {"unsafe", "unsupported"} and not (value["abstain"] or value["needs_clarification"]):
        raise ValueError("Unsafe or unsupported intent must abstain or clarify")
    if value["abstain"] or value["needs_clarification"]:
        if value["sql"] is not None or value["parameters"]:
            raise ValueError("An abstention or clarification must contain no executable SQL or parameters")
    elif value["view"] is None or not value["sql"]:
        raise ValueError("An executable plan requires a view and SQL")
    return value


def plan(intent="unsupported", view=None, sql=None, parameters=None, evidence_fields=None, needs_clarification=False, abstain=False):
    return validate_plan(dict(intent=intent, view=view, sql=sql, parameters=parameters or {}, evidence_fields=evidence_fields or [], needs_clarification=needs_clarification, abstain=abstain))


def messages(question, retrieved):
    return [{"role": "system", "content": json.dumps({"task": "Return exactly one JSON query plan for synthetic supply-chain analysis.",
            "as_of": AS_OF, "allowed_views": list(SCHEMA), "constraints": ["SELECT only", "No writes, external data, DDL or external actions", "Use named $parameters", "Clarify unsupported or ambiguous requests", "Only documented columns and joins", "Never invent evidence or recommendations"], "output_schema": PLAN_SCHEMA})},
            {"role": "user", "content": json.dumps({"question": question, "retrieved_schema": retrieved})}]


class RulePlanner:
    name = "deterministic_rules"

    def generate(self, question, retrieved):
        q = question.strip().lower().rstrip("?.!")
        if re.search(r"\b(delete|drop|truncate|insert|update|alter|attach|copy|install|load|buy|purchase|ship|send)\b", q):
            return plan("unsafe", abstain=True)
        if re.fullmatch(r"which suppliers had the highest late[- ]delivery rate last month", q):
            return plan("supplier_delay", "shipment_view", "SELECT supplier_id, supplier_name, count(is_late) AS evaluable_shipments, sum(CASE WHEN is_late THEN 1 ELSE 0 END) AS late_shipments, avg(CASE WHEN is_late THEN 1.0 WHEN is_late = false THEN 0.0 END) AS late_delivery_rate FROM shipment_view WHERE promised_date >= $start_date AND promised_date < $end_date GROUP BY supplier_id, supplier_name HAVING count(is_late) > 0 ORDER BY late_delivery_rate DESC", {"start_date": "2026-08-01", "end_date": "2026-09-01"}, ["supplier_id", "evaluable_shipments", "late_shipments"])
        stockout = re.fullmatch(r"show products likely to stock out within the next (\d+) days", q)
        if stockout and 1 <= int(stockout[1]) <= 90:
            return plan("stockout", "risk_view", "SELECT * FROM risk_view WHERE days_of_cover < $horizon ORDER BY days_of_cover", {"horizon": int(stockout[1])}, ["source_record_id", "on_hand", "avg_daily_demand", "days_of_cover"])
        what_if = re.fullmatch(r"what happens if demand increases by (\d+(?:\.\d+)?)% at warehouse (\d+)", q)
        if what_if and 0 <= float(what_if[1]) <= 200:
            return plan("what_if", "risk_view", "SELECT *, avg_daily_demand * (1 + $increase) AS scenario_daily_demand, days_of_cover / (1 + $increase) AS scenario_days_of_cover FROM risk_view WHERE warehouse_id = $warehouse", {"increase": float(what_if[1])/100, "warehouse": "W"+what_if[2]}, ["source_record_id", "on_hand", "avg_daily_demand"])
        supplier = re.fullmatch(r"why is supplier ([a-z]) considered high risk", q)
        if supplier:
            return plan("supplier_risk", "supplier_view", "SELECT * FROM supplier_view WHERE supplier_name = $supplier", {"supplier": "Supplier "+supplier[1].upper()}, ["source_record_id", "late_delivery_rate", "evaluable_shipments"])
        shipments = re.fullmatch(r"show shipments for supplier ([a-z])", q)
        if shipments:
            return plan("shipments", "shipment_view", "SELECT * FROM shipment_view WHERE supplier_name = $supplier", {"supplier": "Supplier "+shipments[1].upper()}, ["source_record_id", "promised_date", "delivered_date"])
        if q == "show shipments missing promised delivery dates":
            return plan("shipments", "shipment_view", "SELECT * FROM shipment_view WHERE promised_date IS NULL", evidence_fields=["source_record_id", "promised_date"])
        demand = re.fullmatch(r"forecast demand for product (p\d+) at warehouse (\d+)", q)
        if demand:
            return plan("forecast", "demand_view", "SELECT * FROM demand_view WHERE product_id = $product AND warehouse_id = $warehouse ORDER BY demand_date", {"product": demand[1].upper(), "warehouse": "W"+demand[2]}, ["source_record_id", "demand_date", "units"])
        return plan(needs_clarification=True, abstain=True)


class QwenPlanner:
    name = PLANNER_MODEL

    def __init__(self, local_files_only=True, adapter=None):
        import torch
        from transformers import AutoTokenizer, AutoModelForCausalLM
        self.tokenizer = AutoTokenizer.from_pretrained(PLANNER_MODEL, local_files_only=local_files_only)
        self.model = AutoModelForCausalLM.from_pretrained(PLANNER_MODEL, local_files_only=local_files_only, torch_dtype="auto", device_map="auto")
        if adapter:
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, adapter)
        self.model.eval()

    def generate(self, question, retrieved):
        import torch
        inputs = self.tokenizer.apply_chat_template(messages(question, retrieved), add_generation_prompt=True, return_tensors="pt", return_dict=True).to(self.model.device)
        with torch.inference_mode():
            outputs = self.model.generate(**inputs, max_new_tokens=512, do_sample=False, max_time=30, pad_token_id=self.tokenizer.eos_token_id)
        return self.tokenizer.decode(outputs[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True)
