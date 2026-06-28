#!/usr/bin/env bash
# Provision the Azure VM for ANIMA. Idempotent-ish; safe to re-run.
# Prereqs: az CLI logged in (`az account show`). Run from anywhere.
#
#   RG=anima-rg LOCATION=eastus VM_NAME=anima-eva VM_SIZE=Standard_B2s \
#   ADMIN_USER=azureuser bash deploy/azure/provision.sh
#
# Security model: public IP + Cloudflare orange-cloud proxy.
#   - 443 inbound allowed ONLY from Cloudflare's published IP ranges.
#   - 22 inbound allowed ONLY from your current public IP ($ADMIN_IP).
#   - everything else denied by NSG default.
set -euo pipefail

RG="${RG:-anima-rg}"
LOCATION="${LOCATION:-eastus}"
VM_NAME="${VM_NAME:-anima-eva}"
VM_SIZE="${VM_SIZE:-Standard_B2s}"          # B2s=4GB (floor); B2ms=8GB (comfortable)
ADMIN_USER="${ADMIN_USER:-azureuser}"
IMAGE="${IMAGE:-Ubuntu2204}"                 # Canonical Ubuntu 22.04 LTS
NSG="${VM_NAME}-nsg"
ADMIN_IP="${ADMIN_IP:-$(curl -fsS https://api.ipify.org)}"

echo "== subscription =="; az account show --query "{name:name, id:id}" -o tsv

echo "== resource group: $RG ($LOCATION) =="
az group create -n "$RG" -l "$LOCATION" -o none

echo "== NSG: $NSG =="
az network nsg create -g "$RG" -n "$NSG" -o none

echo "-- SSH (22) from admin IP $ADMIN_IP only --"
az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-ssh-admin \
  --priority 1000 --direction Inbound --access Allow --protocol Tcp \
  --destination-port-ranges 22 --source-address-prefixes "$ADMIN_IP" -o none

echo "-- HTTPS (443) from Cloudflare IP ranges only --"
mapfile -t CF_IPS < <(curl -fsS https://www.cloudflare.com/ips-v4)
echo "   Cloudflare ranges: ${#CF_IPS[@]}"
az network nsg rule create -g "$RG" --nsg-name "$NSG" -n allow-https-cloudflare \
  --priority 1010 --direction Inbound --access Allow --protocol Tcp \
  --destination-port-ranges 443 --source-address-prefixes "${CF_IPS[@]}" -o none

echo "== VM: $VM_NAME ($VM_SIZE) =="
az vm create -g "$RG" -n "$VM_NAME" \
  --image "$IMAGE" --size "$VM_SIZE" \
  --admin-username "$ADMIN_USER" --generate-ssh-keys \
  --public-ip-sku Standard --nsg "$NSG" \
  --os-disk-size-gb 32 -o none

PUBIP=$(az vm show -d -g "$RG" -n "$VM_NAME" --query publicIps -o tsv)
echo
echo "================================================================"
echo " VM ready.  Public IP: $PUBIP"
echo " SSH:  ssh ${ADMIN_USER}@${PUBIP}"
echo " Next: point Cloudflare A record eva.<domain> -> $PUBIP (orange cloud),"
echo "       then run deploy/azure/bootstrap.sh on the VM."
echo "================================================================"
