"""C5-a campaign-family data layer (campaign batching groups)."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass(frozen=True)
class CampaignFamily:
    site: str
    part_no: str
    mold_id: str
    product_family: str
    color: str
    spec: str
    campaign_group: str
    sequence_priority: int
    note: str = ""


def load_campaign_families(path: str | Path, site: Optional[str] = None) -> Dict[str, CampaignFamily]:
    out: Dict[str, CampaignFamily] = {}
    p = Path(path)
    if not p.exists():
        return out
    with p.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row_site = (row.get("Site") or "").strip()
            part = (row.get("Part No.") or "").strip()
            if not row_site or not part or (site and row_site != site):
                continue
            try:
                priority = int(float(row.get("Sequence Priority") or 0))
            except ValueError:
                priority = 0
            out[part] = CampaignFamily(
                site=row_site,
                part_no=part,
                mold_id=(row.get("Mold ID") or "").strip(),
                product_family=(row.get("Product Family") or "").strip(),
                color=(row.get("Color") or "").strip(),
                spec=(row.get("Spec") or "").strip(),
                campaign_group=(row.get("Campaign Group") or "").strip(),
                sequence_priority=priority,
                note=(row.get("Note") or "").strip(),
            )
    return out
