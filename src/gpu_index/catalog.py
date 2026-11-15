"""Which machines count, and how many GPUs each one has.

Prices are always normalised to USD per GPU-hour, so an 8x H100 machine at $55/h
and a 1x H100 machine at $6.88/h become comparable. Anything not listed here is ignored:
being explicit beats guessing from instance names.
"""

from __future__ import annotations

from dataclasses import dataclass

GPUS = ("H100", "A100", "RTX4090")


@dataclass(frozen=True)
class Machine:
    gpu: str        # one of GPUS
    gpu_model: str  # the exact part, e.g. "H100 SXM 80GB"
    gpu_count: int


# Azure: https://learn.microsoft.com/azure/virtual-machines/sizes/overview
# Left out on purpose: confidential-compute (NCC*), "flex"/no-InfiniBand variants and
# dedicated-host meters, which carry pricing premiums unrelated to the GPU itself.
AZURE: dict[str, Machine] = {
    "Standard_NC40ads_H100_v5": Machine("H100", "H100 NVL 94GB", 1),
    "Standard_NC80adis_H100_v5": Machine("H100", "H100 NVL 94GB", 2),
    "Standard_ND96isr_H100_v5": Machine("H100", "H100 SXM 80GB", 8),
    "Standard_NC24ads_A100_v4": Machine("A100", "A100 PCIe 80GB", 1),
    "Standard_NC48ads_A100_v4": Machine("A100", "A100 PCIe 80GB", 2),
    "Standard_NC96ads_A100_v4": Machine("A100", "A100 PCIe 80GB", 4),
    "Standard_ND96asr_A100_v4": Machine("A100", "A100 SXM 40GB", 8),
    "Standard_ND96amsr_A100_v4": Machine("A100", "A100 SXM 80GB", 8),
}


# AWS: https://aws.amazon.com/ec2/instance-types/p5/ and /p4/
# Left out on purpose: p5e/p5en (H200, a different GPU) and Capacity Blocks
# (pre-booked slots priced by auction, listed as $0 in the price list).
AWS: dict[str, Machine] = {
    "p5.48xlarge": Machine("H100", "H100 SXM 80GB", 8),
    "p5.4xlarge": Machine("H100", "H100 SXM 80GB", 1),
    "p4d.24xlarge": Machine("A100", "A100 SXM 40GB", 8),
    "p4de.24xlarge": Machine("A100", "A100 SXM 80GB", 8),
}
