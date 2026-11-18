#!/bin/sh
set -eu
raw=data/raw/phase4
mkdir -p "$raw"
download() {
  name=$1
  identifier=$2
  if [ ! -f "$raw/$name.zip" ]; then
    curl -L --fail -sS "https://www.kaggle.com/api/v1/datasets/download/$identifier" -o "$raw/$name.part"
    mv "$raw/$name.part" "$raw/$name.zip"
  fi
}
download retail anirudhchauhan/retail-store-inventory-forecasting-dataset
download shipment apoorvwatsky/supply-chain-shipment-pricing-data
