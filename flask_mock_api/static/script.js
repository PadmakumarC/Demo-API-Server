
async function loadShipments(){
  const res = await fetch('/api/shipments');
  const shipments = await res.json();
  const select = document.getElementById('shipmentSelect');
  select.innerHTML = '';
  shipments.forEach(s=>{
    const opt=document.createElement('option');
    opt.value=s.shipment_id;
    opt.textContent=`${s.shipment_id} — ${s.origin} → ${s.destination} (${s.weight_kg} kg)`;
    select.appendChild(opt);
  });
}

async function loadOptimization(){
  const shipmentId=document.getElementById('shipmentSelect').value;
  if(!shipmentId) return;
  const res=await fetch(`/api/optimization/${shipmentId}`);
  const data=await res.json();

  const current=data.current;
  const rec=data.recommended||{};

  document.getElementById('currentDetails').innerHTML=`
    <li><strong>Carrier:</strong> ${current.carrier}</li>
    <li><strong>Mode:</strong> ${current.mode}</li>
    <li><strong>Distance:</strong> ${current.distance_km} km</li>
    <li><strong>Emission:</strong> ${current.emission_kg_co2e} kg CO2e</li>
    <li><strong>Cost:</strong> $${current.cost_usd}</li>
  `;

  document.getElementById('recommendedDetails').innerHTML=`
    <li><strong>Carrier:</strong> ${rec.carrier ?? '-'}</li>
    <li><strong>Mode:</strong> ${rec.mode ?? '-'}</li>
    <li><strong>Distance:</strong> ${rec.distance_km ?? '-'}</li>
    <li><strong>Emission:</strong> ${rec.emission_kg_co2e ?? '-'}</li>
    <li><strong>Est. Cost:</strong> ${rec.estimated_cost_usd ?? '-'}</li>
  `;

  const tbody=document.getElementById('altTableBody');
  tbody.innerHTML='';
  (data.alternatives||[]).forEach((a,idx)=>{
    const tr=document.createElement('tr');
    const id=`alt_${idx}`;
    tr.innerHTML=`
      <td>${a.carrier}</td>
      <td>${a.mode}</td>
      <td>${a.distance_km}</td>
      <td>${a.emission_kg_co2e}</td>
      <td>${a.estimated_cost_usd}</td>
      <td><label><input type="radio" name="carrierChoice" value="${a.carrier}" id="${id}"> Choose</label></td>
    `;
    tbody.appendChild(tr);
  });
}

async function submitDecision(){
  const shipmentId=document.getElementById('shipmentSelect').value;
  const decision=document.querySelector('input[name="decision"]:checked').value;
  const comments=document.getElementById('comments').value.trim();
  const chosenRadio=document.querySelector('input[name="carrierChoice"]:checked');
  const chosenCarrier=chosenRadio? chosenRadio.value : null;

  const endpoint = decision==='approve' ? '/api/approve' : '/api/reject';
  const payload = { shipment_id: shipmentId, comments: comments };
  if (decision==='approve') payload.chosen_carrier = chosenCarrier;

  const res = await fetch(endpoint, { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
  const data = await res.json();

  const status = document.getElementById('statusMsg');
  if (data.error) {
    status.textContent = `Error: ${data.error}`;
  } else {
    status.textContent = `Success: ${data.message}`;
    await loadOptimization();
  }
}

document.getElementById('loadBtn').addEventListener('click', loadOptimization);
document.getElementById('submitBtn').addEventListener('click', submitDecision);

(async()=>{
  await loadShipments();
  await loadOptimization();
})();
