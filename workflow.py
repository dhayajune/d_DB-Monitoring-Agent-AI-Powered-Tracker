"""Shared vocabulary for wind turbine installation records, plus a generator for realistic demo events.

Each turbine moves through the installation phases in order. The phase decides the status:
Foundation = OPEN, erection/electrical phases = PROCESSING, Commissioning = COMPLETED.
Any event may log an issue found on site and the fix applied.
"""
import random

STATUSES = ["OPEN", "PROCESSING", "COMPLETED"]

PHASES = ["Foundation", "Tower Erection", "Nacelle Installation", "Rotor & Blade Installation",
          "Electrical & Grid Connection", "Commissioning"]
PHASE_STATUS = {p: "PROCESSING" for p in PHASES}
PHASE_STATUS.update({"Foundation": "OPEN", "Commissioning": "COMPLETED"})

# Fictional wind farms: site -> (turbine ID prefix, location)
SITES = {
    "Kayathar Ridge Wind Park": ("KYR", "Thoothukudi, Tamil Nadu, India"),
    "Kutch Coastal Wind Farm": ("KCW", "Kutch, Gujarat, India"),
    "Thar Desert Wind Park": ("TDW", "Jaisalmer, Rajasthan, India"),
    "Emsland North Wind Farm": ("EMN", "Lower Saxony, Germany"),
    "Soria Highlands Wind Park": ("SHW", "Castilla y Leon, Spain"),
    "Panhandle Plains Wind Project": ("PPW", "Amarillo, Texas, USA"),
}
TURBINE_MODELS = ["5.7 MW - 163 m rotor", "4.8 MW - 149 m rotor", "4.5 MW - 155 m rotor", "3.6 MW - 131 m rotor"]
TECHNICIANS = ["arjun.v", "priya.n", "karthik.s", "lena.m", "jonas.w", "carlos.r", "meera.d"]

NOTES = {
    "Foundation": ["Anchor cage installed and surveyed", "Foundation concrete pour completed", "Earthing ring laid"],
    "Tower Erection": ["Tower sections 1-3 erected", "Top tower section installed", "Flange bolts torqued to spec"],
    "Nacelle Installation": ["Nacelle lifted and bolted to yaw bearing", "Nacelle installed, yaw system checked"],
    "Rotor & Blade Installation": ["Hub and three blades installed", "Single-blade installation completed"],
    "Electrical & Grid Connection": ["MV cable pulled and terminated", "Transformer energised", "SCADA link established"],
    "Commissioning": ["Turbine commissioned and handed over", "Performance test passed, handed over to operations"],
}
# phase -> [(issue, fix)]
ISSUES = {
    "Foundation": [("Anchor cage misaligned by 4 mm", "Realigned with hydraulic jacks and re-surveyed"),
                   ("Concrete cube test result pending", "Cube test passed at 28 days")],
    "Tower Erection": [("Flange gap above tolerance on section 2", "Shimmed and re-torqued flange bolts"),
                       ("Wind speed above lifting limit", "Lift completed in next weather window")],
    "Nacelle Installation": [("Crane pad ground settlement", "Crane mats added and crane re-levelled"),
                             ("Grease leak at yaw bearing seal", "Seal replaced and area cleaned")],
    "Rotor & Blade Installation": [("Leading edge protection damage on blade B", "LEP repaired on site"),
                                   ("Blade root bolt torque deviation", "All blade root bolts re-torqued")],
    "Electrical & Grid Connection": [("Low insulation resistance on MV cable", "Cable re-terminated, IR test passed"),
                                     ("Fibre optic link loss to SCADA", "Fibre spliced, link restored")],
    "Commissioning": [("Pitch encoder calibration offset", "Pitch encoders recalibrated"),
                      ("Grid code test to be repeated", "Grid compliance test passed on re-run")],
}


def next_phase(phase: str | None) -> str | None:
    """Phase that follows `phase` (None = not started yet); None once commissioned."""
    if phase is None:
        return PHASES[0]
    i = PHASES.index(phase) if phase in PHASES else len(PHASES) - 1
    return PHASES[i + 1] if i + 1 < len(PHASES) else None


def new_turbine(site: str | None = None) -> dict:
    site = site or random.choice(list(SITES))
    prefix, location = SITES[site]
    return {"turbine_id": f"{prefix}-WTG-{random.randint(1, 60):02d}", "site_name": site,
            "location": location, "turbine_model": random.choice(TURBINE_MODELS)}


def make_event(turbine: dict, phase: str, issue_chance: float = 0.3) -> dict:
    """One installation event for `turbine` at `phase`. Some events log an issue; most issues are fixed."""
    issue = fix = None
    if random.random() < issue_chance:
        issue, fix = random.choice(ISSUES[phase])
        if phase != "Commissioning" and random.random() < 0.25:
            fix = None  # still being worked on
    return {**turbine, "installation_phase": phase, "technician": random.choice(TECHNICIANS),
            "status": PHASE_STATUS[phase], "notes": random.choice(NOTES[phase]), "issue": issue, "fix_applied": fix}


COLUMNS = ["turbine_id", "site_name", "location", "turbine_model", "installation_phase", "technician",
           "status", "notes", "issue", "fix_applied", "processed_at"]
INSERT_SQL = (f"INSERT INTO workflow_details ({', '.join(COLUMNS)}) "
              f"VALUES ({', '.join(['%s'] * len(COLUMNS))})")
