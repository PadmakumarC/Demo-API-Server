
# flask_mock_api/app.py
from flask import Flask, jsonify, request
import json, os

app = Flask(__name__)

# ---------- Paths for local JSON "database" ----------
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
SHIPMENTS_FILE = os.path.join(DATA_DIR, 'shipments.json')
CARRIERS_FILE = os.path.join(DATA_DIR, 'carriers.json')
DISTANCES_FILE = os.path.join(DATA_DIR, 'distances.json')
EMISSION_FILE = os.path.join(DATA_DIR, 'emission_factors.json')


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
        current_carrier = carrier_lookup(s.get('carrier'))
        mode = current_carrier['mode'] if current_carrier else None

        if 'original_carrier' not in s:
            s['original_carrier'] = s.get('carrier')
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


# ---------- Routes ----------

@app.get('/health')
def health():
    """Basic health check endpoint."""
    return {"status": "ok"}, 200


@app.get('/api/shipments')
def get_shipments():
    """
    Return all shipments, enriched with the transport `mode` based on the carrier.
    If the carrier is unknown/missing, `mode` will be None.
    """
    shipments = load_json(SHIPMENTS_FILE)
    enriched = []
    for s in shipments:
        c = carrier_lookup(s.get('carrier'))
        s_with_mode = dict(s)
        s_with_mode['mode'] = c['mode'] if c else None
        enriched.append(s_with_mode)
    return jsonify(enriched)


@app.get('/api/shipments/<shipment_id>')
def get_shipment(shipment_id):
    shipments = load_json(SHIPMENTS_FILE)
    for s in shipments:
        if s['shipment_id'] == shipment_id:
            # Enrich single shipment with mode as well for consistency
            c = carrier_lookup(s.get('carrier'))
            s_enriched = dict(s)
            s_enriched['mode'] = c['mode'] if c else None
            return jsonify(s_enriched)
    return jsonify({"error": "Not found"}), 404


@app.get('/api/shipments/<shipment_id>/mode')
def get_shipment_mode(shipment_id):
    """
    Optional helper: return just the mode for a shipment (derived from carrier).
    """
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
    - an ad-hoc payload (origin, destination, weight_kg, [carrier], [mode])

    Preference: if the caller provides `mode`, it overrides the carrier-derived mode.
    """
    data = request.get_json(force=True)
    shipment_id = data.get('shipment_id')

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
    """
    Compare current carrier vs alternatives for a shipment:
    - Returns current (with mode, cost, emission)
    - Returns alternatives list (with mode, cost, emission)
    - Returns a recommended option (min emission; tie -> min cost)
    """
    shipments = load_json(SHIPMENTS_FILE)
    shipment = next((s for s in shipments if s['shipment_id'] == shipment_id), None)
    if not shipment:
        return jsonify({"error": "Shipment not found"}), 404

    origin = shipment['origin']; destination = shipment['destination']
    weight_kg = shipment['weight_kg']
    distance_km = get_distance(origin, destination)

    current_carrier = carrier_lookup(shipment.get('carrier'))
    if current_carrier:
        current_emission = calc_emission(weight_kg, distance_km, mode=current_carrier['mode'])
        current_cost = shipment.get('cost_usd', calc_cost(distance_km, current_carrier['base_cost_per_km']))
        current_mode = current_carrier['mode']
    else:
        current_emission = calc_emission(weight_kg, distance_km)
        current_cost = shipment.get('cost_usd', 0)
        current_mode = None

    alternatives = []
    for alt in list_alternative_carriers(exclude=shipment.get('carrier')):
        alt_emission = calc_emission(weight_kg, distance_km, mode=alt['mode'])
        alt_cost = calc_cost(distance_km, alt['base_cost_per_km'])
        alternatives.append({
            "carrier": alt['name'],
            "mode": alt['mode'],
            "distance_km": distance_km,
            "emission_kg_co2e": alt_emission,
            "estimated_cost_usd": alt_cost
        })

    # Recommend by lowest emission, then lowest cost
    best = sorted(alternatives, key=lambda x: (x['emission_kg_co2e'], x['estimated_cost_usd']))[0] if alternatives else None

    return jsonify({
        "shipment_id": shipment_id,
        "current": {
            "carrier": shipment.get('carrier'),
            "mode": current_mode,
            "distance_km": distance_km,
            "emission_kg_co2e": current_emission,
            "cost_usd": current_cost
        },
        "alternatives": alternatives,
        "recommended": best
    })


@app.post('/api/approve')
def approve():
    """
    Record approval; optionally switch to a chosen_carrier and recompute current cost/emission.
    """
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
            cur_carrier = carrier_lookup(s.get('carrier'))
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
    """
    Record rejection; recompute current cost/emission to reflect existing carrier details.
    """
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
            cur_carrier = carrier_lookup(s.get('carrier'))
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
    """
    Return aggregate KPIs and per-shipment deltas,
    ensuring baseline fields exist first.
    """
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


# ---------- Entrypoint for local run (Render uses Gunicorn) ----------

if __name__ == '__main__':
    # Initialize baselines and run locally (Render will use gunicorn in production)
    ensure_baselines()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
