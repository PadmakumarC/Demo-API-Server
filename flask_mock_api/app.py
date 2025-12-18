# flask_mock_api/app.py
from flask import Flask, jsonify, request
import json, os, random, uuid
from datetime import datetime

app = Flask(__name__)

# Paths for local JSON "database"
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SHIPMENTS_FILE = os.path.join(DATA_DIR, 'shipments.json')
CARRIERS_FILE = os.path.join(DATA_DIR, 'carriers.json')
DISTANCES_FILE = os.path.join(DATA_DIR, 'distances.json')
EMISSION_FILE = os.path.join(DATA_DIR, 'emission_factors.json')

# Dynamic mode controls
DYNAMIC_MODE = os.getenv('DYNAMIC_MODE', 'false').lower() in ('1', 'true', 'yes')
DEFAULT_RANDOM_COUNT = int(os.getenv('DEFAULT_RANDOM_COUNT', '25'))


# ---------- Utility functions ----------

def load_json(path):
    """Load a JSON file, returning Python objects."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_json(path, data):
    """Persist Python objects to a JSON file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

def get_distance(origin, destination):
    """Lookup distance in km between two locations, fallback to 1000km if unknown."""
    distances = load_json(DISTANCES_FILE)
    key = f"{origin}-{destination}"
    return distances.get(key, 1000)

def carrier_lookup(name):
    """Return carrier dict by name, or None if not found."""
    carriers = load_json(CARRIERS_FILE)
    for c in carriers:
        if c['name'] == name:
            return c
    return None

def list_alternative_carriers(exclude=None):
    """Return all carriers except the one named in exclude."""
    carriers = load_json(CARRIERS_FILE)
    return [c for c in carriers if c['name'] != exclude]

def calc_emission(weight_kg, distance_km, mode=None, factor_override=None):
    """
    Calculate kg CO2e based on weight (tons), distance (km), and emission factor.
    Emission factor (ef) pulled from emission_factors.json by mode, default 0.1 if missing.
    """
    tons = weight_kg / 1000.0
    if factor_override is not None:
        ef = factor_override
    else:
        ef_map = load_json(EMISSION_FILE)
        ef = ef_map.get(mode, 0.1)
    return round(tons * distance_km * ef, 2)

def calc_cost(distance_km, base_cost_per_km):
    """Simple cost model: distance * base_cost_per_km."""
    return round(distance_km * base_cost_per_km, 2)

def ensure_baselines():
    """
    Populate baseline fields for shipments:
    - original_carrier, original_cost_usd, original_emission_kg_co2e
    - current_cost_usd, current_emission_kg_co2e
    Saves back to shipments.json if any changes.
    """
    shipments = load_json(SHIPMENTS_FILE)
    changed = False
    for s in shipments:
        origin = s['origin']; destination = s['destination']
        distance_km = get_distance(origin, destination)
        weight_kg = s['weight_kg']
        current_carrier = carrier_lookup(s['carrier'])
        mode = current_carrier['mode'] if current_carrier else None

        if 'original_carrier' not in s:
            s['original_carrier'] = s['carrier']
            s['original_cost_usd'] = s.get(
                'cost_usd',
                calc_cost(distance_km, current_carrier['base_cost_per_km'] if current_carrier else 0)
            )
            s['original_emission_kg_co2e'] = calc_emission(weight_kg, distance_km, mode=mode)
            changed = True

        if 'current_cost_usd' not in s or 'current_emission_kg_co2e' not in s:
            s['current_cost_usd'] = s.get(
                'cost_usd',
                calc_cost(distance_km, current_carrier['base_cost_per_km'] if current_carrier else 0)
            )
            s['current_emission_kg_co2e'] = calc_emission(weight_kg, distance_km, mode=mode)
            changed = True

    if changed:
        save_json(SHIPMENTS_FILE, shipments)


# ---------- Random generation helpers ----------

def _derive_locations_from_distances():
    """Derive a unique list of locations from DISTANCES_FILE keys."""
    locs = set()
    try:
        distances = load_json(DISTANCES_FILE)
        for k in distances.keys():
            try:
                a, b = k.split('-')
                locs.add(a); locs.add(b)
            except ValueError:
                # Ignore malformed keys
                pass
    except FileNotFoundError:
        pass

    if not locs:
        # Fallback set (tweak as you like)
        locs = {
            'DXB','DOH','DEL','BOM','LHR','LGW','FRA','CDG','AMS','MAD',
            'JFK','EWR','SFO','LAX','ORD','DFW','SEA',
            'SIN','HKG','NRT','ICN','SYD','MEL','YYZ'
        }
    return sorted(locs)

def _make_random_id(rnd: random.Random) -> str:
    return f"SHP{uuid.uuid4().hex[:8].upper()}"

def generate_random_shipments(count=25, seed=None, persist=False):
    """
    Generate random shipments. If persist=True, save to SHIPMENTS_FILE and return list.
    If persist=False, return list without saving.
    """
    rnd = random.Random(seed) if seed is not None else random
    locations = _derive_locations_from_distances()
    carriers_list = []
    try:
        carriers_list = load_json(CARRIERS_FILE)
    except FileNotFoundError:
        carriers_list = []

    shipments = []
    for _ in range(int(count)):
        origin, destination = rnd.sample(locations, 2)
        carrier_obj = rnd.choice(carriers_list) if carriers_list else None
        carrier_name = carrier_obj['name'] if carrier_obj else None
        base_cost_per_km = (
            carrier_obj['base_cost_per_km'] if carrier_obj else round(rnd.uniform(0.2, 1.2), 2)
        )
        weight_kg = rnd.randint(100, 5000)
        shipment_id = _make_random_id(rnd)
        distance_km = get_distance(origin, destination)
        cost_usd = calc_cost(distance_km, base_cost_per_km)

        # Slightly bias to CREATED so your approval flows remain relevant
        status = rnd.choice(['CREATED','APPROVED','REJECTED','CREATED','CREATED'])

        shipments.append({
            'shipment_id': shipment_id,
            'origin': origin,
            'destination': destination,
            'carrier': carrier_name,
            'weight_kg': weight_kg,
            'cost_usd': cost_usd,
            'status': status,
            'created_at': datetime.utcnow().isoformat() + 'Z'
        })

    if persist:
        save_json(SHIPMENTS_FILE, shipments)

    return shipments


# ---------- Routes ----------

@app.get('/health')
def health():
    return {"status": "ok"}, 200


@app.get('/api/shipments')
def get_shipments():
    """
    If DYNAMIC_MODE=true (env) OR ?random=true, return random shipments (no file writes).
    Otherwise return shipments from shipments.json.
    """
    use_random = DYNAMIC_MODE or (request.args.get('random', '').lower() in ('1','true','yes'))
    if use_random:
        count = int(request.args.get('count', DEFAULT_RANDOM_COUNT))
        seed = request.args.get('seed')
        shipments = generate_random_shipments(count=count, seed=seed, persist=False)
        return jsonify(shipments)

    shipments = load_json(SHIPMENTS_FILE)
    return jsonify(shipments)


@app.get('/api/shipments/random')
def get_shipments_random_alias():
    """Alias endpoint: always return random shipments, does not persist."""
    count = int(request.args.get('count', DEFAULT_RANDOM_COUNT))
    seed = request.args.get('seed')
    shipments = generate_random_shipments(count=count, seed=seed, persist=False)
    return jsonify(shipments)


@app.get('/api/shipments/<shipment_id>')
def get_shipment(shipment_id):
    shipments = load_json(SHIPMENTS_FILE)
    for s in shipments:
        if s['shipment_id'] == shipment_id:
            return jsonify(s)
    return jsonify({"error": "Not found"}), 404


@app.post('/api/calculate_emission')
def calculate_emission_endpoint():
    data = request.get_json(force=True)
    shipment_id = data.get('shipment_id')

    if shipment_id:
        shipments = load_json(SHIPMENTS_FILE)
        shipment = next((s for s in shipments if s['shipment_id'] == shipment_id), None)
        if not shipment:
            return jsonify({"error": "Shipment not found"}), 404
        origin = shipment['origin']; destination = shipment['destination']
        weight_kg = shipment['weight_kg']
        carrier = carrier_lookup(shipment['carrier'])
        mode = carrier['mode'] if carrier else None
    else:
        origin = data.get('origin'); destination = data.get('destination')
        weight_kg = data.get('weight_kg')
        carrier_name = data.get('carrier')
        carrier = carrier_lookup(carrier_name) if carrier_name else None
        mode = carrier['mode'] if carrier else data.get('mode')

    distance_km = get_distance(origin, destination)
    emission = calc_emission(weight_kg, distance_km, mode=mode)

    return jsonify({
        "origin": origin,
        "destination": destination,
        "weight_kg": weight_kg,
        "mode": mode,
        "distance_km": distance_km,
        "emission_kg_co2e": emission
    })


@app.get('/api/optimization/<shipment_id>')
def optimization(shipment_id):
    shipments = load_json(SHIPMENTS_FILE)
    shipment = next((s for s in shipments if s['shipment_id'] == shipment_id), None)
    if not shipment:
        return jsonify({"error": "Shipment not found"}), 404

    origin = shipment['origin']; destination = shipment['destination']
    weight_kg = shipment['weight_kg']
    distance_km = get_distance(origin, destination)

    current_carrier = carrier_lookup(shipment['carrier'])
    if current_carrier:
        current_emission = calc_emission(weight_kg, distance_km, mode=current_carrier['mode'])
        current_cost = shipment.get('cost_usd', calc_cost(distance_km, current_carrier['base_cost_per_km']))
    else:
        current_emission = calc_emission(weight_kg, distance_km)
        current_cost = shipment.get('cost_usd', 0)

    alternatives = []
    for alt in list_alternative_carriers(exclude=shipment['carrier']):
        alt_emission = calc_emission(weight_kg, distance_km, mode=alt['mode'])
        alt_cost = calc_cost(distance_km, alt['base_cost_per_km'])
        alternatives.append({
            "carrier": alt['name'],
            "mode": alt['mode'],
            "distance_km": distance_km,
            "emission_kg_co2e": alt_emission,
            "estimated_cost_usd": alt_cost
        })

    best = sorted(
        alternatives,
        key=lambda x: (x['emission_kg_co2e'], x['estimated_cost_usd'])
    )[0] if alternatives else None

    return jsonify({
        "shipment_id": shipment_id,
        "current": {
            "carrier": shipment['carrier'],
            "mode": current_carrier['mode'] if current_carrier else None,
            "distance_km": distance_km,
            "emission_kg_co2e": current_emission,
            "cost_usd": current_cost
        },
        "alternatives": alternatives,
        "recommended": best
    })


@app.post('/api/approve')
def approve():
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
            if chosen_carrier:
                s['carrier'] = chosen_carrier
            cur_carrier = carrier_lookup(s['carrier'])
            if cur_carrier:
                s['current_cost_usd'] = s.get('cost_usd', calc_cost(distance_km, cur_carrier['base_cost_per_km']))
                s['current_emission_kg_co2e'] = calc_emission(weight_kg, distance_km, mode=cur_carrier['mode'])
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
            cur_carrier = carrier_lookup(s['carrier'])
            if cur_carrier:
                s['current_cost_usd'] = s.get('cost_usd', calc_cost(distance_km, cur_carrier['base_cost_per_km']))
                s['current_emission_kg_co2e'] = calc_emission(weight_kg, distance_km, mode=cur_carrier['mode'])
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
    shipments = load_json(SHIPMENTS_FILE)
    ensure_baselines()

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
            'original_emission_kg_co2e': s.get('original_emission_kg_co2e'),
            'current_emission_kg_co2e': s.get('current_emission_kg_co2e'),
            'original_cost_usd': s.get('original_cost_usd'),
            'current_cost_usd': s.get('current_cost_usd'),
            'emission_delta': round((s.get('current_emission_kg_co2e', 0) - s.get('original_emission_kg_co2e', 0)), 2),
            'cost_delta': round((s.get('current_cost_usd', 0) - s.get('original_cost_usd', 0)), 2)
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


@app.post('/api/seed')
def seed_random_shipments():
    """
    Regenerate shipments.json with random shipments and initialize baselines.
    Body: {"count": 50, "seed": 123}  (both optional)
    """
    data = request.get_json(silent=True) or {}
    count = int(data.get('count', DEFAULT_RANDOM_COUNT))
    seed = data.get('seed')
    shipments = generate_random_shipments(count=count, seed=seed, persist=True)
    ensure_baselines()
    return jsonify({
        "message": "seeded",
        "count": len(shipments),
               "sample_ids": [s['shipment_id'] for s in shipments[:5]]
    })


if __name__ == '__main__':
    # Initialize baselines and run locally (Render will use gunicorn)
    # For local dev: uncomment to pre-seed once
    # generate_random_shipments(count=DEFAULT_RANDOM_COUNT, seed=42, persist=True)
    ensure_baselines()
    port = int(os.environ.get('PORT', 5000))
