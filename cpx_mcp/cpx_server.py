# cpx_server.py — a complete, working MCP server. You do NOT change this logic;
# you READ it, RUN it, and COMMENT every block (see the task after the code).
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

mcp = FastMCP("cpx-validator")

# Reference data. Oxides maps each oxide to its (molar mass, number of cations, number of oxygen atoms) - OXIDES
# Maps oxide to the single metal/metalloid element in it - OXIDE_TO_ELEMENT
# Default acceptance thresholds - DEFAULT_TOL
# The tools must derive everything from oxide wt% because that is what the electron microprobe acturally measures.
# Hard coding values and promotes determinism 
OXIDES = {
    "SiO2": (60.08, 1, 2), "TiO2": (79.87, 1, 2), "Al2O3": (101.96, 2, 3),
    "Cr2O3": (151.99, 2, 3), "FeO": (71.84, 1, 1), "MnO": (70.94, 1, 1),
    "MgO": (40.30, 1, 1), "CaO": (56.08, 1, 1), "Na2O": (61.98, 2, 1),
    "K2O": (94.20, 2, 1),
}

OXIDE_TO_ELEMENT = {
    "SiO2": "Si", "TiO2": "Ti", "Al2O3": "Al", "Cr2O3": "Cr", "FeO": "Fe",
    "MnO": "Mn", "MgO": "Mg", "CaO": "Ca", "Na2O": "Na", "K2O": "K",
}

DEFAULT_TOL = {"charge_rel": 0.01, "site_abs": 0.05, "total_min": 98.5, "total_max": 101.0}


@mcp.tool
def validate_cpx_analysis(analysis: dict, tolerances: dict | None = None) -> dict:
    """Recalculate a clinopyroxene microprobe analysis to cations per 6 oxygens,
    recover Fe3+/Fe2+ by charge balance (Droop 1987), assign sites, and return a
    structured feasibility report. Computes ground truth from the raw oxide inputs
    ONLY — never trusts any caller-supplied formula or 'feasible' flag."""
    tol = {**DEFAULT_TOL, **(tolerances or {})}

    # Computes every known oxide as a non-negative float and takes the sum of all
    # the wt%. This is done to clean the data of any negatives and other garbage.
    # Should sum near 100 (check 1)
    clean = {}
    for ox in OXIDES:
        value = float(analysis.get(ox, 0.0))
        if value < 0:
            raise ToolError(f"{ox} weight-percent is negative")
        clean[ox] = value
    analytical_total = sum(clean.values())

    # Computes moles of that oxide (moles), moles of oxygen (mol_O), total cation for
    # each element (cat[elem]), normalizes to cations per 6 oxygens, and finally
    # total cations per formula unit. Mineral formulas are conventionally written
    # per a fixed number of oxygens, and for clinopyroxene, 6. Normalizes so that
    # charge balances are comparable to known pyroxene stoichometry. The total cation
    # count, S, is used to drive the Fe3+ estimates
    mol_O = 0.0
    cat = {e: 0.0 for e in ["Si", "Ti", "Al", "Cr", "Fe", "Mn", "Mg", "Ca", "Na", "K"]}
    for ox, (molar_mass, n_cat, n_oxy) in OXIDES.items():
        moles = clean[ox] / molar_mass
        mol_O += moles * n_oxy
        cat[OXIDE_TO_ELEMENT[ox]] += moles * n_cat
    if mol_O <= 0:
        raise ToolError("analysis has no oxygen-bearing oxides")
    norm = 6.0 / mol_O
    pfu = {e: c * norm for e, c in cat.items()}
    S = sum(pfu.values())

    # Splits total iron Fe3+ and Fe2+ based on the value of S based on Droop (1987). The
    # microprobe reports Fe but not in its oxidation state, but the charge balance depends
    # on it. Using Droop's paper, solve for Fe3_unclamped - which is the theoretical amount of
    # Fe3+ while Fe3 is the physically possible range. The rest is Fe2. If raw Fe3+ value exceeds
    # Fe total, fail check 3
    Fe_total = pfu["Fe"]
    Fe3_unclamped = 12.0 * (1.0 - 4.0 / S)
    Fe3 = min(Fe_total, max(0.0, Fe3_unclamped))
    Fe2 = Fe_total - Fe3

    # Assigning calculated cations into clinopyroxene's three crystallographic sites (T, M1, M2).
    # Pulls the Si, Al cation counts, Al_IV fills the tetrahedral site (T) which holds 2 cations
    # using Si first. Any Al needed to top T up to 2 goes in as tetra. Al (Al_IV). Al_VI is the
    # leftover Al going into the ochtahedral site. T is total tetrahedral cation (check 4). Should 
    # be = 2. M1 is the ochtahedral M1 site and M2 is the larger 8-fold M2 site. M1, M2 should = 0. 
    Si = pfu["Si"]
    Al = pfu["Al"]
    Al_IV = min(Al, max(0.0, 2.0 - Si))
    Al_VI = Al - Al_IV
    T = Si + Al_IV
    M1 = Al_VI + pfu["Ti"] + pfu["Cr"] + Fe3 + pfu["Mg"] + Fe2 + pfu["Mn"]
    M2 = pfu["Ca"] + pfu["Na"] + pfu["K"]

    # Calculates tje total positive charge of all cations and checks against fixed negative charge
    # of the 6 oxygens. Charge sum should be +12, so residual is 0 - This is for check 5.
    charge_sum = (4 * Si + 4 * pfu["Ti"] + 3 * Al + 3 * pfu["Cr"] + 3 * Fe3 + 2 * Fe2
                  + 2 * pfu["Mn"] + 2 * pfu["Mg"] + 2 * pfu["Ca"] + pfu["Na"] + pfu["K"])
    residual = charge_sum - 12.0

    # List of 6 pass/fail with diagnostics
    # 1: wt% = 100 within threshold (bad measurement)
    # 2: Si can't exceed the T-side capacity (stoichimetric impossibility)
    # 3: raw Fe3+ within 0, Fe Total (oxidation-state imposibility)
    # 4: tetrahederal site is full (structrual misfit)
    # 5: charge residual within charge_rel * 12 (electrostatic imbalance)
    # 6: M1 and M2 each near 1 (calculated cations fit the expected crystal structure)
    checks = [
        {"name": "analytical_total",
         "ok": tol["total_min"] <= analytical_total <= tol["total_max"],
         "value": round(analytical_total, 3), "limit": [tol["total_min"], tol["total_max"]]},
        {"name": "silica_le_2", "ok": Si <= 2.0 + tol["site_abs"], "Si_pfu": round(Si, 4)},
        {"name": "iron_split_valid", "ok": 0.0 <= Fe3_unclamped <= Fe_total + 1e-9,
         "Fe3_pfu": round(Fe3_unclamped, 4), "Fe_total_pfu": round(Fe_total, 4)},
        {"name": "T_fills_to_2", "ok": abs(T - 2.0) <= tol["site_abs"], "T": round(T, 4)},
        {"name": "charge_balance", "ok": abs(residual) <= tol["charge_rel"] * 12.0,
         "residual": round(residual, 4)},
        {"name": "site_totals",
         "ok": abs(M1 - 1.0) <= tol["site_abs"] and abs(M2 - 1.0) <= tol["site_abs"],
         "M1": round(M1, 4), "M2": round(M2, 4)},
    ]

    # Checks which violation is violated first, if any at all. Useful for debugging
    first_violation = next((c["name"] for c in checks if not c["ok"]), None)
    feasible = first_violation is None

    # JSON-serializable report with useful diagnostic metrics
    return {
        "feasible": feasible,
        "analytical_total": round(analytical_total, 3),
        "cations_pfu": {"Si": round(Si, 4), "Al_IV": round(Al_IV, 4), "Al_VI": round(Al_VI, 4),
                        "Ti": round(pfu["Ti"], 4), "Cr": round(pfu["Cr"], 4),
                        "Fe3": round(Fe3, 4), "Fe2": round(Fe2, 4), "Mn": round(pfu["Mn"], 4),
                        "Mg": round(pfu["Mg"], 4), "Ca": round(pfu["Ca"], 4),
                        "Na": round(pfu["Na"], 4), "K": round(pfu["K"], 4)},
        "site_totals": {"T": round(T, 4), "M1": round(M1, 4), "M2": round(M2, 4),
                        "sum": round(T + M1 + M2, 4)},
        "charge_sum": round(charge_sum, 4),
        "charge_residual": round(residual, 4),
        "checks": checks,
        "first_violation": first_violation,
        "message": "Feasible clinopyroxene analysis." if feasible
                   else f"Infeasible: failed {first_violation}.",
    }


if __name__ == "__main__":
    mcp.run()    # default transport: stdio