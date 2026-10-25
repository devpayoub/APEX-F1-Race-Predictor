"""Central configuration: circuit database, model constants, paths."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
DASHBOARD_DIR = ROOT / "dashboard"
CACHE_DIR = Path(os.environ.get("F1_CACHE_DIR", Path.home() / ".cache" / "f1predictor"))

MODEL_VERSION = "1.0.0"

# ---------------------------------------------------------------- physics ----
FUEL_EFFECT = 0.030          # s per kg of fuel on board
START_FUEL_KG = 100.0        # race start fuel load
PRACTICE_FUEL_KG = 60.0      # typical long-run fuel load
FUEL_BURN_PER_LAP = 1.79     # kg/lap, used to de-trend stint slopes
RACE_PACE_OFFSET = 2.60      # quali -> race pace offset (s), dry, medium tyre

# ------------------------------------------------------------ degradation ----
DEG_PRIOR = 0.075            # circuit prior (s/lap) before shrinkage
DEG_SHRINK = 0.50            # weight on the measured team slope vs the prior
DEG_CLAMP = (0.030, 0.140)

# ------------------------------------------------------------ model weights ---
BLEND_MC = 0.60
BLEND_ML = 0.40
CALIBRATION_T = 1.30           # set from the season backtest (favourite was over-confident)
WIN_SHRINK = 0.0             # shrinkage toward uniform; disabled (flattens the tail)
PACE_W_LONG_RUN = 0.30
PACE_W_QUALI = 0.70
LR_SHRINK = 0.50             # shrink noisy long-run deltas toward zero
LR_CLAMP = (-1.5, 2.5)       # plausible long-run delta band (seconds)
DEFAULT_SIMS = 20000

# ---------------------------------------------------------- race simulation --
DIRTY_AIR_WINDOW = 0.90      # seconds; DRS-era following distance
DIRTY_AIR_PENALTY = 0.10     # seconds lost per lap stuck in traffic
RACE_FORM_SIGMA = 0.050      # s/lap per-race setup/form variation
PASS_WINDOW = 0.55           # seconds; close enough to attempt a pass
PASS_BASE = 0.12             # base pass probability per lap when in range
GRID_GAP = 1.60              # seconds of track position per grid slot at lights out
POINTS_POSITIONS = 10
ML_SOFTEN_T = 1.30           # temperature applied to the ML prior before blending

# ------------------------------------------------------------- compounds -----
COMPOUND_DEG_MULT = {"SOFT": 1.45, "MEDIUM": 1.00, "HARD": 0.65,
                     "INTERMEDIATE": 1.10, "WET": 0.90}
# seconds added to a lap to express it as MEDIUM-equivalent pace
COMPOUND_PACE_OFFSET = {"SOFT": 0.55, "MEDIUM": 0.0, "HARD": -0.45,
                        "INTERMEDIATE": 6.0, "WET": 12.0}
COMPOUND_COLOUR = {"SOFT": "#E8002D", "MEDIUM": "#FFD400", "HARD": "#F0F0F0",
                   "INTERMEDIATE": "#3FBF3F", "WET": "#2E7BD6"}

# --------------------------------------------------------------- circuits ----
# lap counts are the official race distance; pit_loss in seconds; sc_prob per race
CIRCUITS: dict[str, dict] = {
    "americas":        dict(laps=56, length_km=5.513, pit_loss=21.5, sc_prob=0.35, overtake=0.72,
                            name="Circuit of the Americas", country="United States", locality="Austin"),
    "rodriguez":       dict(laps=71, length_km=4.304, pit_loss=20.0, sc_prob=0.40, overtake=0.55,
                            name="Autódromo Hermanos Rodríguez", country="Mexico", locality="Mexico City"),
    "interlagos":      dict(laps=71, length_km=4.309, pit_loss=19.5, sc_prob=0.45, overtake=0.80,
                            name="Autódromo José Carlos Pace", country="Brazil", locality="São Paulo"),
    "vegas":           dict(laps=50, length_km=6.201, pit_loss=20.5, sc_prob=0.30, overtake=0.90,
                            name="Las Vegas Strip Circuit", country="United States", locality="Las Vegas"),
    "losail":          dict(laps=57, length_km=5.419, pit_loss=21.0, sc_prob=0.25, overtake=0.60,
                            name="Lusail International Circuit", country="Qatar", locality="Lusail"),
    "yas_marina":      dict(laps=58, length_km=5.281, pit_loss=20.5, sc_prob=0.30, overtake=0.65,
                            name="Yas Marina Circuit", country="United Arab Emirates", locality="Abu Dhabi"),
    "monza":           dict(laps=53, length_km=5.793, pit_loss=21.0, sc_prob=0.30, overtake=0.85,
                            name="Autodromo Nazionale di Monza", country="Italy", locality="Monza"),
    "spa":             dict(laps=44, length_km=7.004, pit_loss=20.0, sc_prob=0.40, overtake=0.78,
                            name="Circuit de Spa-Francorchamps", country="Belgium", locality="Spa"),
    "silverstone":     dict(laps=52, length_km=5.891, pit_loss=20.5, sc_prob=0.35, overtake=0.70,
                            name="Silverstone Circuit", country="United Kingdom", locality="Silverstone"),
    "monaco":          dict(laps=78, length_km=3.337, pit_loss=19.0, sc_prob=0.50, overtake=0.12,
                            name="Circuit de Monaco", country="Monaco", locality="Monte Carlo"),
    "red_bull_ring":   dict(laps=71, length_km=4.318, pit_loss=19.5, sc_prob=0.35, overtake=0.72,
                            name="Red Bull Ring", country="Austria", locality="Spielberg"),
    "hungaroring":     dict(laps=70, length_km=4.381, pit_loss=19.0, sc_prob=0.30, overtake=0.28,
                            name="Hungaroring", country="Hungary", locality="Budapest"),
    "zandvoort":       dict(laps=72, length_km=4.259, pit_loss=19.5, sc_prob=0.35, overtake=0.30,
                            name="Circuit Zandvoort", country="Netherlands", locality="Zandvoort"),
    "baku":            dict(laps=51, length_km=6.003, pit_loss=20.0, sc_prob=0.50, overtake=0.70,
                            name="Baku City Circuit", country="Azerbaijan", locality="Baku"),
    "marina_bay":      dict(laps=62, length_km=4.940, pit_loss=21.5, sc_prob=0.55, overtake=0.35,
                            name="Marina Bay Street Circuit", country="Singapore", locality="Singapore"),
    "suzuka":          dict(laps=53, length_km=5.807, pit_loss=20.0, sc_prob=0.30, overtake=0.45,
                            name="Suzuka International Racing Course", country="Japan", locality="Suzuka"),
    "shanghai":        dict(laps=56, length_km=5.451, pit_loss=21.0, sc_prob=0.30, overtake=0.62,
                            name="Shanghai International Circuit", country="China", locality="Shanghai"),
    "bahrain":         dict(laps=57, length_km=5.412, pit_loss=21.0, sc_prob=0.25, overtake=0.55,
                            name="Bahrain International Circuit", country="Bahrain", locality="Sakhir"),
    "jeddah":          dict(laps=50, length_km=6.174, pit_loss=19.5, sc_prob=0.45, overtake=0.68,
                            name="Jeddah Corniche Circuit", country="Saudi Arabia", locality="Jeddah"),
    "albert_park":     dict(laps=58, length_km=5.278, pit_loss=20.0, sc_prob=0.40, overtake=0.60,
                            name="Albert Park Circuit", country="Australia", locality="Melbourne"),
    "miami":           dict(laps=57, length_km=5.412, pit_loss=20.5, sc_prob=0.35, overtake=0.66,
                            name="Miami International Autodrome", country="United States", locality="Miami"),
    "imola":           dict(laps=63, length_km=4.909, pit_loss=19.5, sc_prob=0.30, overtake=0.30,
                            name="Autodromo Enzo e Dino Ferrari", country="Italy", locality="Imola"),
    "catalunya":       dict(laps=66, length_km=4.657, pit_loss=20.0, sc_prob=0.25, overtake=0.42,
                            name="Circuit de Barcelona-Catalunya", country="Spain", locality="Barcelona"),
    "villeneuve":      dict(laps=70, length_km=4.361, pit_loss=19.0, sc_prob=0.30, overtake=0.55,
                            name="Circuit Gilles Villeneuve", country="Canada", locality="Montréal"),
}
DEFAULT_CIRCUIT = dict(laps=57, length_km=5.4, pit_loss=20.5, sc_prob=0.35, overtake=0.60,
                       name="Unknown Circuit", country="", locality="")

# ------------------------------------------------------------------- HTTP ----
HTTP_TIMEOUT = 45
HTTP_RETRIES = 3
OPENF1_THROTTLE = 0.12
USER_AGENT = "f1-predictor/1.0 (research; contact: local)"
