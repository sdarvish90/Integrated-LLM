# capex_deployments.py

generic_capex_profile = {
    "year_-2_H1": 0.04,
    "year_-2_H2": 0.04,
    "year_-1_H1": 0.30,
    "year_-1_H2": 0.30,
    "year_0_H1":  0.32,
}

# Aggregate to full years
CAPEX_DEPLOYMENT = {}

for k, v in generic_capex_profile.items():
    # k example: "year_-2_H1"
    year = int(k.split("_")[1])   # → -2, -1, 0
    CAPEX_DEPLOYMENT[year] = CAPEX_DEPLOYMENT.get(year, 0.0) + float(v)

# Sanity check
_total = sum(CAPEX_DEPLOYMENT.values())
if abs(_total - 1.0) > 1e-6:
    raise ValueError(f"CAPEX deployment fractions must sum to 1.0 (got {_total})")
