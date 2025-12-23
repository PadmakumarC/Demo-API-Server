# flask_mock_api/app.py
from flask import Flask, jsonify, request
import json, os, random, uuid
from datetime import datetime, date

app = Flask(__name__)

# ---------- Paths for local JSON "database" ----------
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SHIPMENTS_FILE = os.path.join(DATA_DIR, 'shipments.json')
CARRIERS_FILE = os.path.join(DATA_DIR, 'carriers.json')
DISTANCES_FILE = os.path.join(DATA_DIR, 'distances.json')
EMISSION_FILE = os.path.join(DATA_DIR, 'emission_factors.json')

# ---------- Dynamic controls ----------
DYNAMIC_MODE = os.getenv('DYNAMIC_MODE', 'false').lower() in ('1', 'true', 'yes')
DEFAULT_RANDOM_COUNT = int(os.getenv('DEFAULT_RANDOM_COUNT', '25'))

# ---------- Utility functions ----------

def load_json(path):
    """Load a JSON file, returning Python objects."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    """Persist Python objects to a JSON file (ensures parent directory)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def get_distance(origin, destination):
    """Lookup distance in km between two locations, fallback to 1000km if unknown."""
    try:
        distances = load_json(DISTANCES_FILE)
    except FileNotFoundError:
        distances = {}
    key = f"{origin}-{destination}"
    return distances.get(key, 1000)

def carrier_lookup(name):
    """Return carrier dict by name, or None if not found."""
    try:
        carriers = load_json(CARRIERS_FILE)
    except FileNotFoundError:
        carriers = []
    for c in carriers:
        if c.get('name') == name:
            return c
    return None

def list_alternative_carriers(exclude=None):
    """Return all carriers except the one named in exclude."""
    try:
        carriers = load_json(CARRIERS_FILE)
    except FileNotFoundError:
        carriers = []
    return [c for c in carriers if c.get('name') != exclude]

def get_emission_factor(mode):
    """
    Return (factor, source) for a given mode.
    If missing, default to 0.1 and annotate the source accordingly.
    """
    try:
        ef_map = load_json(EMISSION_FILE)
    except FileNotFoundError:
        ef_map = {}
    if mode and mode in ef_map:
        try:
            val = float(ef_map[mode])
        except Exception:
            val = 0.1
        return val, "internal:emission_factors.json"
    return 0.1, "default:0.1 (missing or unknown mode)"

# NEW: emission with provenance (method, factor, source, weights)
def calc_emission_with_provenance(weight_kg, distance_km, mode=None, factor_override=None):
    """
    Calculate emission and include provenance fields:
      - method, weight_tons, distance_km, mode,
      - emission_factor_kgco2e_per_ton_km, factor_source
    """
    tons = float(weight_kg) / 1000.0
    if factor_override is not None:
        ef = float(factor_override)
        source = "override"
    else:
        ef, source = get_emission_factor(mode)
    emission = round(tons * float(distance_km) * float(ef), 2)
    details = {
        "method": "weight_tons * distance_km * emission_factor_kgco2e_per_ton_km",
        "weight_tons": round(tons, 6),
        "distance_km": float(distance_km),
        "mode": mode,
        "emission_factor_kgco2e_per_ton_km": float(ef),
        "factor_source": source
    }
    return emission, details

def calc_emission(weight_kg, distance_km, mode=None, factor_override=None):
    """Backward-compatible: just the number."""
    emission, _ = calc_emission_with_provenance(weight_kg, distance_km, mode, factor_override)
    return emission

def calc_cost(distance_km, base_cost_per_km, surcharges_usd=0.0):
    """Cost model: distance * base_cost_per_km + surcharges."""
    return round(float(distance_km) * float(base_cost_per_km) + float(surcharges_usd), 2)

# NEW: default transit days by mode (can be overridden in carriers.json with avg_transit_days)
def default_transit_days_for_mode(mode):
    mapping = {'air': 2, 'road': 5, 'rail': 4, 'sea': 14}
    return mapping.get((mode or '').lower(), 5)

def ensure_baselines():
    """
    Populate and persist baseline fields for shipments:
    - distance_km
    - original_carrier, original_cost_usd, original_emission_kg_co2e, original_emission_details
    - current_cost_usd, current_emission_kg_co2e, current_emission_details
    """
    try:
        shipments = load_json(SHIPMENTS_FILE)
    except FileNotFoundError:
        return
    changed = False
    for s in shipments:
        origin = s['origin']; destination = s['destination']
        distance_km = get_distance(origin, destination)
        s['distance_km'] = distance_km  # NEW: persist distance
        weight_kg = s['weight_kg']
        current_carrier = carrier_lookup(s.get('carrier'))
        mode = current_carrier['mode'] if current_carrier else None
        base_cost = current_carrier['base_cost_per_km'] if current_carrier else 0

        if 'original_carrier' not in s:
            s['original_carrier'] = s.get('carrier')
            s['original_cost_usd'] = s.get('cost_usd', calc_cost(distance_km, base_cost))
            emi_val, emi_det = calc_emission_with_provenance(weight_kg, distance_km, mode=mode)
            s['original_emission_kg_co2e'] = emi_val
            s['original_emission_details'] = emi_det  # NEW
            changed = True

        if 'current_cost_usd' not in s or 'current_emission_kg_co2e' not in s:
            s['current_cost_usd'] = s.get('cost_usd', calc_cost(distance_km, base_cost))
            emi_val, emi_det = calc_emission_with_provenance(weight_kg, distance_km, mode=mode)
            s['current_emission_kg_co2e'] = emi_val
            s['current_emission_details'] = emi_det  # NEW
            changed = True

    if changed:
        save_json(SHIPMENTS_FILE, shipments)

# ---------- Random generation helpers ----------

def _derive_locations_from_distances():
    """Derive a unique list of locations from DISTANCES_FILE keys, with sensible fallbacks."""
    locs = set()
    try:
        distances = load_json(DISTANCES_FILE)
        for k in distances.keys():
            try:
                a, b = k.split('-')
                locs.add(a.strip()); locs.add(b.strip())
            except Exception:
                pass
    except FileNotFoundError:
        pass
    if not locs:
        locs = {
            'Dubai','Mumbai','Berlin','London','Paris','Frankfurt','Amsterdam','Madrid',
            'Doha','Delhi','Bangalore','Hyderabad','Chennai','Singapore','Hong Kong'
        }
    return sorted(locs)

def _load_carriers_safe():
    try:
        carriers = load_json(CARRIERS_FILE)
    except FileNotFoundError:
        carriers = [
            {"name": "AirFast",  "mode": "air",  "base_cost_per_km": 0.55, "avg_transit_days": 2},
            {"name": "RoadGulf", "mode": "road", "base_cost_per_km": 0.16, "avg_transit_days": 5},
            {"name": "RailEuro", "mode": "rail", "base_cost_per_km": 0.12, "avg_transit_days": 4},
            {"name": "SeaWise",  "mode": "sea",  "base_cost_per_km": 0.10, "avg_transit_days": 14},
        ]
    return carriers

def _make_random_id(rnd: random.Random) -> str:
    return f"SHP{uuid.uuid4().hex[:8].upper()}"

def generate_random_shipments(count=25, seed=None):
    """
    Generate random shipments (no persistence). Includes `mode` derived from chosen carrier,
    and enriches with distance and emission provenance for demo clarity.
    """
    rnd = random.Random(seed) if seed is not None else random
    locations = _derive_locations_from_distances()
    carriers_list = _load_carriers_safe()

    shipments = []
    for _ in range(int(count)):
        origin, destination = rnd.sample(locations, 2)
        carrier_obj = rnd.choice(carriers_list)
        carrier_name = carrier_obj['name']
        base_cost_per_km = float(carrier_obj['base_cost_per_km'])
        mode = carrier_obj['mode']

        weight_kg = rnd.randint(200, 5000)
        shipment_id = _make_random_id(rnd)
        distance_km = get_distance(origin, destination)
        cost_usd = calc_cost(distance_km, base_cost_per_km)

        status = rnd.choice(['CREATED','CREATED','APPROVED','REJECTED'])  # bias to CREATED
        delivery_date = (datetime.utcnow()).strftime('%Y-%m-%d')

        emi_val, emi_det = calc_emission_with_provenance(weight_kg, distance_km, mode=mode)

        shipments.append({
            'shipment_id': shipment_id,
            'origin': origin,
            'destination': destination,
            'carrier': carrier_name,
            'mode': mode,
            'weight_kg': weight_kg,
            'distance_km': distance_km,          # NEW
            'cost_usd': cost_usd,
            'status': status,
            'delivery_date': delivery_date,
            'created_at': datetime.utcnow().isoformat() + 'Z',
            'original_carrier': carrier_name,
            'original_cost_usd': cost_usd,
            'original_emission_kg_co2e': emi_val,
            'original_emission_details': emi_det, # NEW
            'current_cost_usd': cost_usd,
            'current_emission_kg_co2e': emi_val,
            'current_emission_details': emi_det   # NEW
        })

    return shipments

# ---------- Policy helpers (NEW) ----------

def parse_policy_from_request(data_or_args):
    """
    Accepts either JSON body (dict) or request.args (MultiDict) and returns a normalized policy dict.
    Supported fields:
      - sla_due_date (YYYY-MM-DD)
      - sla_priority ('critical'|'normal')
      - budget_cap_usd (float)
      - emission_reduction_min_pct (float, default 30)
      - budget_increase_max_pct (float, default 10)
    """
    get = (data_or_args.get if hasattr(data_or_args, 'get') else lambda k, d=None: data_or_args.get(k, d))
    policy = {
        "sla_due_date": get("sla_due_date"),
        "sla_priority": (get("sla_priority") or "normal"),
        "budget_cap_usd": float(get("budget_cap_usd")) if get("budget_cap_usd") is not None else None,
        "emission_reduction_min_pct": float(get("emission_reduction_min_pct", 30)),
        "budget_increase_max_pct": float(get("budget_increase_max_pct", 10))
    }
    # Validate date
    if policy["sla_due_date"]:
        try:
            datetime.strptime(policy["sla_due_date"], "%Y-%m-%d").date()
        except Exception:
            policy["sla_due_date"] = None
    return policy

def compute_transit_days_for_carrier(carrier_obj):
    if not carrier_obj:
        return None
    return int(carrier_obj.get("avg_transit_days", default_transit_days_for_mode(carrier_obj.get("mode"))))

def sla_met(transit_days, sla_due_date_str):
    """Simple SLA: if arrival date (today + transit_days) <= due date."""
    if not transit_days or not sla_due_date_str:
        return None
    try:
        due = datetime.strptime(sla_due_date_str, "%Y-%m-%d").date()
    except Exception:
        return None
    arrival = date.today() + timedelta(days=int(transit_days))
    return arrival <= due

# ---------- Routes ----------

@app.get('/health')
def health():
    return {"status": "ok"}, 200

@app.get('/api/shipments')
def get_shipments():
    """
    Return shipments:
      - If DYNAMIC_MODE=true or ?random=1 → return a fresh random set (no persistence).
        Optional: ?count=NN, ?seed=NNN for deterministic demos.
      - Else → return file-based shipments, enriched with `mode` and `distance_km`.
    """
    use_random = DYNAMIC_MODE or (request.args.get('random', '').lower() in ('1', 'true', 'yes'))
    if use_random:
        count = int(request.args.get('count', DEFAULT_RANDOM_COUNT))
        seed_param = request.args.get('seed', None)
        seed = int(seed_param) if (seed_param is not None and seed_param.isdigit()) else None
        shipments = generate_random_shipments(count=count, seed=seed)
        return jsonify(shipments)

    shipments = load_json(SHIPMENTS_FILE)
    enriched = []
    for s in shipments:
        c = carrier_lookup(s.get('carrier'))
        s_with_mode = dict(s)
        s_with_mode['mode'] = c['mode'] if c else s.get('mode')
        # Ensure distance is present
        s_with_mode['distance_km'] = s.get('distance_km') or get_distance(s['origin'], s['destination'])
        enriched.append(s_with_mode)
    return jsonify(enriched)

@app.get('/api/shipments/<shipment_id>')
def get_shipment(shipment_id):
    shipments = load_json(SHIPMENTS_FILE)
    for s in shipments:
        if s['shipment_id'] == shipment_id:
            c = carrier_lookup(s.get('carrier'))
            s_enriched = dict(s)
            s_enriched['mode'] = c['mode'] if c else s.get('mode')
            s_enriched['distance_km'] = s.get('distance_km') or get_distance(s['origin'], s['destination'])
            return jsonify(s_enriched)
    return jsonify({"error": "Not found"}), 404

@app.get('/api/shipments/<shipment_id>/mode')
def get_shipment_mode(shipment_id):
    """Return just the mode for a shipment (derived from carrier)."""
    shipments = load_json(SHIPMENTS_FILE)
    shipment = next((x for x in shipments if x['shipment_id'] == shipment_id), None)
    if not shipment:
        return jsonify({"error": "Not found"}), 404
    c = carrier_lookup(shipment.get('carrier'))
    return jsonify({
        "shipment_id": shipment_id,
        "carrier": shipment.get('carrier'),
        "mode": (c['mode'] if c else None)
    })

@app.post('/api/calculate_emission')
def calculate_emission_endpoint():
    """
    Calculate emissions for either:
    - a given shipment_id (looks up carrier/mode), or
    - an ad-hoc payload (origin, destination, weight_kg, [carrier], [mode], [emission_factor_kgco2e_per_ton_km])

    Preference: if the caller provides `mode`, it overrides the carrier-derived mode.
    """
    data = request.get_json(force=True)
    shipment_id = data.get('shipment_id')
    factor_override = data.get('emission_factor_kgco2e_per_ton_km')

    if shipment_id:
        shipments = load_json(SHIPMENTS_FILE)
        shipment = next((s for s in shipments if s['shipment_id'] == shipment_id), None)
        if not shipment:
            return jsonify({"error": "Shipment not found"}), 404
        origin = shipment['origin']; destination = shipment['destination']
        weight_kg = shipment['weight_kg']
        carrier = carrier_lookup(shipment.get('carrier'))
        derived_mode = carrier['mode'] if carrier else None
        mode = data.get('mode', derived_mode)  # prefer explicit mode if provided
    else:
        origin = data.get('origin'); destination = data.get('destination')
        weight_kg = data.get('weight_kg')
        carrier_name = data.get('carrier')
        carrier = carrier_lookup(carrier_name) if carrier_name else None
        derived_mode = carrier['mode'] if carrier else None
        mode = data.get('mode', derived_mode)  # prefer explicit mode if provided

    distance_km = get_distance(origin, destination)
    emission, details = calc_emission_with_provenance(weight_kg, distance_km, mode=mode, factor_override=factor_override)

    return jsonify({
        "origin": origin,
        "destination": destination,
        "weight_kg": weight_kg,
        "mode": mode,
        "distance_km": distance_km,
        "emission_kg_co2e": emission,
        "emission_calculation": details  # NEW: explainability
    })

@app.get('/api/optimization/<shipment_id>')
def optimization(shipment_id):
    """
    Compare current carrier vs alternatives for a shipment:
    - Returns current (with mode, cost, emission, transit_days)
    - Returns alternatives list (with mode, cost, emission, transit_days)
    - Returns a recommended option:
        * if policy provided via query: choose option that meets policy, min emission then min cost
        * else: min emission; tie -> min cost
    Optional query params (policy):
      ?sla_due_date=YYYY-MM-DD&sla_priority=critical|normal&budget_cap_usd=650&emission_reduction_min_pct=30&budget_increase_max_pct=10
    """
    shipments = load_json(SHIPMENTS_FILE)
    shipment = next((s for s in shipments if s['shipment_id'] == shipment_id), None)
    if not shipment:
        return jsonify({"error": "Shipment not found"}), 404

    policy = parse_policy_from_request(request.args)
    origin = shipment['origin']; destination = shipment['destination']
    weight_kg = shipment['weight_kg']
    distance_km = get_distance(origin, destination)

    current_carrier = carrier_lookup(shipment.get('carrier'))
    if current_carrier:
        current_emission, current_em_det = calc_emission_with_provenance(weight_kg, distance_km, mode=current_carrier['mode'])
        current_cost = shipment.get('cost_usd', calc_cost(distance_km, current_carrier['base_cost_per_km']))
        current_mode = current_carrier['mode']
        current_transit = compute_transit_days_for_carrier(current_carrier)
    else:
        current_emission, current_em_det = calc_emission_with_provenance(weight_kg, distance_km)
        current_cost = shipment.get('cost_usd', 0)
        current_mode = None
        current_transit = None

    alternatives = []
    carriers = list_alternative_carriers(exclude=shipment.get('carrier'))
    for alt in carriers:
        alt_emission, alt_em_det = calc_emission_with_provenance(weight_kg, distance_km, mode=alt['mode'])
        alt_cost = calc_cost(distance_km, alt['base_cost_per_km'])
        alt_transit = compute_transit_days_for_carrier(alt)

        # Optional policy evaluation
        policy_eval = {}
        if policy:
            # Emission reduction % vs current
            if current_emission:
                red_pct = round((current_emission - alt_emission) / current_emission * 100, 2)
            else:
                red_pct = None
            budget_ok = None
            if policy.get('budget_cap_usd') is not None:
                budget_ok = alt_cost <= float(policy['budget_cap_usd'])
            budget_increase_ok = None
            if current_cost and policy.get('budget_increase_max_pct') is not None:
                budget_increase_ok = (alt_cost <= current_cost * (1 + policy['budget_increase_max_pct'] / 100.0))
            sla_ok = None
            if policy.get('sla_due_date') and alt_transit is not None:
                # SLA met if arrival before due date
                try:
                    due = datetime.strptime(policy['sla_due_date'], "%Y-%m-%d").date()
                    arrival = date.today() + timedelta(days=int(alt_transit))
                    sla_ok = arrival <= due
                except Exception:
                    sla_ok = None

            policy_eval = {
                "emission_reduction_pct_vs_current": red_pct,
                "meets_min_emission_reduction": (red_pct is not None and red_pct >= policy['emission_reduction_min_pct']),
                "within_budget_cap": budget_ok,
                "within_budget_increase_pct": budget_increase_ok,
                "sla_met": sla_ok
            }

        alternatives.append({
            "carrier": alt['name'],
            "mode": alt['mode'],
            "distance_km": distance_km,
            "emission_kg_co2e": alt_emission,
            "emission_calculation": alt_em_det,  # NEW
            "estimated_cost_usd": alt_cost,
            "transit_days": alt_transit,
            "policy_alignment": policy_eval if policy else None
        })

    # Recommendation logic
    candidates = alternatives[:]
    # If policy present, pre-filter to those meeting policy (all applicable booleans True)
    if policy and current_emission:
        def meets_policy(x):
            pa = x.get('policy_alignment') or {}
            checks = []
            # Emission reduction
            checks.append(pa.get('meets_min_emission_reduction') is True)
            # Budget: if both cap and increase pct provided, both must be OK; if only one provided, that one must be OK
            cap = pa.get('within_budget_cap')
            inc = pa.get('within_budget_increase_pct')
            if policy.get('budget_cap_usd') is not None and policy.get('budget_increase_max_pct') is not None:
                checks.append(cap is True and inc is True)
            elif policy.get('budget_cap_usd') is not None:
                checks.append(cap is True)
            elif policy.get('budget_increase_max_pct') is not None:
                checks.append(inc is True)
            # SLA (if due provided)
            if policy.get('sla_due_date'):
                checks.append(pa.get('sla_met') is True)
            return all(checks) if checks else True

        filtered = [x for x in candidates if meets_policy(x)]
        if filtered:
            candidates = filtered

    recommended = None
    if candidates:
        recommended = sorted(candidates, key=lambda x: (x['emission_kg_co2e'], x['estimated_cost_usd']))[0]

    return jsonify({
        "shipment_id": shipment_id,
        "policy": policy if any(policy.values()) else None,
        "current": {
            "carrier": shipment.get('carrier'),
            "mode": current_mode,
            "distance_km": distance_km,
            "emission_kg_co2e": current_emission,
            "emission_calculation": current_em_det,  # NEW
            "cost_usd": current_cost,
            "transit_days": current_transit
        },
        "alternatives": alternatives,
        "recommended": recommended
    })

# NEW: scenario simulation endpoint for Agent reasoning
@app.post('/api/shipments/<shipment_id>/simulate')
def simulate(shipment_id):
    """
    Simulate an alternative scenario for a shipment.
    Request JSON:
    {
      "mode": "rail",                // optional
      "carrier": "RailEuro",         // optional
      "distance_km": 2100,           // optional (defaults to O-D distance)
      "emission_factor_kgco2e_per_ton_km": 0.034, // optional
      "expected_transit_days": 4,    // optional (else carrier/default)
      "base_cost_per_km": 0.12,      // optional (else carrier)
      "surcharges_usd": 20,          // optional (default 0)
      "policy": {                    // optional policy to evaluate recommendation fit
        "sla_due_date": "2025-12-22",
        "sla_priority": "critical",
        "budget_cap_usd": 650,
        "emission_reduction_min_pct": 30,
        "budget_increase_max_pct": 10
      }
    }
    Response includes comparison vs current and policy alignment.
    """
    data = request.get_json(force=True)
    shipments = load_json(SHIPMENTS_FILE)
    shipment = next((s for s in shipments if s['shipment_id'] == shipment_id), None)
    if not shipment:
        return jsonify({"error": "Shipment not found"}), 404

    policy = parse_policy_from_request(data.get('policy') or {})

    origin = shipment['origin']; destination = shipment['destination']
    weight_kg = shipment['weight_kg']
    base_distance = get_distance(origin, destination)

    # Current baseline
    current_carrier = carrier_lookup(shipment.get('carrier'))
    current_mode = current_carrier['mode'] if current_carrier else None
    current_cost = shipment.get('cost_usd', calc_cost(base_distance, current_carrier['base_cost_per_km'] if current_carrier else 0))
    current_transit = compute_transit_days_for_carrier(current_carrier)
    current_emission, current_em_det = calc_emission_with_provenance(weight_kg, base_distance, mode=current_mode)

    # Scenario overrides
    scenario_carrier_name = data.get('carrier')
    scenario_carrier = carrier_lookup(scenario_carrier_name) if scenario_carrier_name else None
    scenario_mode = data.get('mode') or (scenario_carrier.get('mode') if scenario_carrier else current_mode)
    scenario_distance = float(data.get('distance_km', base_distance))
    scenario_factor = data.get('emission_factor_kgco2e_per_ton_km')
    scenario_transit = data.get('expected_transit_days') or (compute_transit_days_for_carrier(scenario_carrier) if scenario_carrier else default_transit_days_for_mode(scenario_mode))
    base_cost_per_km = data.get('base_cost_per_km') or (scenario_carrier.get('base_cost_per_km') if scenario_carrier else (current_carrier.get('base_cost_per_km') if current_carrier else 0))
    surcharges_usd = float(data.get('surcharges_usd', 0))

    scenario_emission, scenario_em_det = calc_emission_with_provenance(weight_kg, scenario_distance, mode=scenario_mode, factor_override=scenario_factor)
    scenario_cost = calc_cost(scenario_distance, base_cost_per_km, surcharges_usd=surcharges_usd)

    # Comparison
    emission_delta_pct = round(((scenario_emission - current_emission) / current_emission) * 100, 2) if current_emission else None
    cost_delta_usd = round(scenario_cost - current_cost, 2)

    # Policy alignment
    policy_alignment = None
    if policy:
        red_pct = None
        if current_emission:
            red_pct = round((current_emission - scenario_emission) / current_emission * 100, 2)
        sla_ok = None
        if policy.get('sla_due_date') and scenario_transit is not None:
            try:
                due = datetime.strptime(policy['sla_due_date'], "%Y-%m-%d").date()
                arrival = date.today() + timedelta(days=int(scenario_transit))
                sla_ok = arrival <= due
            except Exception:
                sla_ok = None
        budget_cap_ok = (policy.get('budget_cap_usd') is not None and scenario_cost <= float(policy['budget_cap_usd']))
        budget_increase_ok = (current_cost and policy.get('budget_increase_max_pct') is not None and scenario_cost <= current_cost * (1 + policy['budget_increase_max_pct'] / 100.0))

        policy_alignment = {
            "emission_reduction_pct_vs_current": red_pct,
            "meets_min_emission_reduction": (red_pct is not None and red_pct >= policy['emission_reduction_min_pct']),
            "within_budget_cap": budget_cap_ok if policy.get('budget_cap_usd') is not None else None,
            "within_budget_increase_pct": budget_increase_ok if policy.get('budget_increase_max_pct') is not None else None,
            "sla_met": sla_ok
        }

    return jsonify({
        "shipment_id": shipment_id,
        "scenario": {
            "mode": scenario_mode,
            "carrier": (scenario_carrier_name or shipment.get('carrier')),
            "distance_km": scenario_distance,
            "emission_kg_co2e": scenario_emission,
            "emission_calculation": scenario_em_det,
            "cost_usd": scenario_cost,
            "transit_days": scenario_transit
        },
        "current": {
            "carrier": shipment.get('carrier'),
            "mode": current_mode,
            "distance_km": base_distance,
            "emission_kg_co2e": current_emission,
            "emission_calculation": current_em_det,
            "cost_usd": current_cost,
            "transit_days": current_transit
        },
        "comparison_vs_current": {
            "emission_delta_pct": emission_delta_pct,
            "cost_delta_usd": cost_delta_usd
        },
        "policy_alignment": policy_alignment,
        "policy": policy if any(policy.values()) else None
    })

@app.post('/api/approve')
def approve():
    """Record approval and recompute current cost/emission for chosen carrier (with provenance)."""
    data = request.get_json(force=True)
    shipment_id = data.get('shipment_id')
    chosen_carrier = data.get('chosen_carrier')
    comments = data.get('comments', '')

    shipments = load_json(SHIPMENTS_FILE)
    updated = False
    for s in shipments:
        if s['shipment_id'] == shipment_id:
            origin = s['origin']; destination = s['destination']
            weight_kg = s['weight_kg']
            distance_km = get_distance(origin, destination)
            s['distance_km'] = distance_km  # NEW

            if chosen_carrier:
                s['carrier'] = chosen_carrier
            cur_carrier = carrier_lookup(s.get('carrier'))
            if cur_carrier:
                s['current_cost_usd'] = s.get('cost_usd', calc_cost(distance_km, cur_carrier['base_cost_per_km']))
                emi_val, emi_det = calc_emission_with_provenance(weight_kg, distance_km, mode=cur_carrier['mode'])
                s['current_emission_kg_co2e'] = emi_val
                s['current_emission_details'] = emi_det  # NEW
            s['status'] = 'APPROVED'
            s['approver_comments'] = comments
            updated = True
            break

    if not updated:
        return jsonify({"error": "Shipment not found"}), 404

    save_json(SHIPMENTS_FILE, shipments)
    return jsonify({
        "message": "Approval recorded",
        "shipment_id": shipment_id,
        "chosen_carrier": chosen_carrier,
        "comments": comments
    })

@app.post('/api/reject')
def reject():
    """Record rejection and recompute current cost/emission for existing carrier (with provenance)."""
    data = request.get_json(force=True)
    shipment_id = data.get('shipment_id')
    comments = data.get('comments', '')

    shipments = load_json(SHIPMENTS_FILE)
    updated = False
    for s in shipments:
        if s['shipment_id'] == shipment_id:
            origin = s['origin']; destination = s['destination']
            weight_kg = s['weight_kg']
            distance_km = get_distance(origin, destination)
            s['distance_km'] = distance_km  # NEW

            cur_carrier = carrier_lookup(s.get('carrier'))
            if cur_carrier:
                s['current_cost_usd'] = s.get('cost_usd', calc_cost(distance_km, cur_carrier['base_cost_per_km']))
                emi_val, emi_det = calc_emission_with_provenance(weight_kg, distance_km, mode=cur_carrier['mode'])
                s['current_emission_kg_co2e'] = emi_val
                s['current_emission_details'] = emi_det  # NEW

            s['status'] = 'REJECTED'
            s['approver_comments'] = comments
            updated = True
            break

    if not updated:
        return jsonify({"error": "Shipment not found"}), 404

    save_json(SHIPMENTS_FILE, shipments)
    return jsonify({
        "message": "Rejection recorded",
        "shipment_id": shipment_id,
        "comments": comments
    })

@app.get('/api/dashboard')
def dashboard_metrics():
    """Return aggregate KPIs and per-shipment deltas; ensure baselines first."""
    ensure_baselines()
    shipments = load_json(SHIPMENTS_FILE)

    total = len(shipments)
    approved = sum(1 for s in shipments if s.get('status') == 'APPROVED')
    rejected = sum(1 for s in shipments if s.get('status') == 'REJECTED')
    pending = total - approved - rejected

    total_emission_original = sum(s.get('original_emission_kg_co2e', 0) for s in shipments)
    total_emission_current = sum(s.get('current_emission_kg_co2e', 0) for s in shipments)
    total_cost_original = sum(s.get('original_cost_usd', 0) for s in shipments)
    total_cost_current = sum(s.get('current_cost_usd', 0) for s in shipments)

    reduction = round(total_emission_original - total_emission_current, 2)
    reduction_pct = round((reduction / total_emission_original * 100), 2) if total_emission_original else 0
    cost_delta = round(total_cost_current - total_cost_original, 2)

    details = []
    for s in shipments:
        details.append({
            'shipment_id': s['shipment_id'],
            'origin': s['origin'],
            'destination': s['destination'],
            'status': s.get('status', 'CREATED'),
            'original_carrier': s.get('original_carrier'),
            'current_carrier': s.get('carrier'),
            'distance_km': s.get('distance_km'),  # NEW
            'original_emission_kg_co2e': s.get('original_emission_kg_co2e'),
            'current_emission_kg_co2e': s.get('current_emission_kg_co2e'),
            'original_cost_usd': s.get('original_cost_usd'),
            'current_cost_usd': s.get('current_cost_usd'),
            'emission_delta': round((s.get('current_emission_kg_co2e', 0) - s.get('original_emission_kg_co2e', 0)), 2),
            'cost_delta': round((s.get('current_cost_usd', 0) - s.get('original_cost_usd', 0)), 2),
            'current_emission_details': s.get('current_emission_details'),     # NEW
            'original_emission_details': s.get('original_emission_details')    # NEW
        })

    return jsonify({
        'summary': {
            'total_shipments': total,
            'approved': approved,
            'rejected': rejected,
            'pending': pending,
            'total_emission_original': round(total_emission_original, 2),
            'total_emission_current': round(total_emission_current, 2),
            'total_emission_reduction': reduction,
            'total_emission_reduction_pct': reduction_pct,
            'total_cost_original': round(total_cost_original, 2),
            'total_cost_current': round(total_cost_current, 2),
            'total_cost_delta': cost_delta
        },
        'shipments': details
    })

# ---------- Entrypoint for local run (Render uses Gunicorn) ----------
if __name__ == '__main__':
    try:
        ensure_baselines()
    except Exception:
        pass
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)