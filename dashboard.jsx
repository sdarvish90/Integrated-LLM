import React, { useState, useMemo } from 'react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, Sankey, PieChart, Pie, Cell, LineChart, Line, ComposedChart, Area } from 'recharts';
import { Fuel, Zap, Flame, Battery, Car, Truck, Factory, Droplets, Wind, AlertTriangle, CheckCircle } from 'lucide-react';

// ============================================================
// CALCULATION ENGINE (simplified from Python model)
// ============================================================

const calculateTriGen = (NG_scf_h, config = {}) => {
  const {
    electrical_eff = 0.50,
    overall_eff = 0.80,
    H2_coprod_share = 0.223,
    LHV_NG_BTU_scf = 1037,
    LHV_H2_kWh_kg = 33.33,
    aux_load_frac = 0.03,
    AC_DC_rate = 0.94,
  } = config;

  if (NG_scf_h === 0) return { H2_kg_day: 0, elec_kWh_day: 0, heat_kW_day: 0, NG_scf_day: 0 };

  const fuel_energy_kW = NG_scf_h * LHV_NG_BTU_scf / 3412;
  const fuel_to_stack = fuel_energy_kW * (1 - H2_coprod_share);
  const H2_energy_kW = fuel_energy_kW * H2_coprod_share;
  const H2_kg_h = H2_energy_kW / LHV_H2_kWh_kg;
  const power_gross = Math.max(1400, electrical_eff * fuel_to_stack);
  const power_net = power_gross * (1 - aux_load_frac);
  const useful_heat = (overall_eff - electrical_eff) * fuel_energy_kW;
  
  // Simplified heat export calculation
  const heat_export_kW = useful_heat * 0.5;

  return {
    H2_kg_day: H2_kg_h * 24,
    elec_kWh_day: power_net * 24 * AC_DC_rate,
    heat_kW_day: heat_export_kW * 24,
    NG_scf_day: NG_scf_h * 24,
  };
};

const calculateSMR = (H2_tpd, heat_from_trigen = 0) => {
  if (H2_tpd === 0) return { H2_kg_day: 0, NG_scf_day: 0, CO2_kg_day: 0, elec_kWh_day: 0 };
  
  const H2_kg_day = H2_tpd * 1000;
  const H2_kg_h = H2_kg_day / 24;
  const NG_kg_h = H2_kg_h * 3; // 3 kg CH4 per kg H2
  const NG_scf_h = NG_kg_h / 0.02007;
  const CO2_kg_h = (NG_kg_h / 16.04) * 44.01;
  
  return {
    H2_kg_day,
    NG_scf_day: NG_scf_h * 24,
    CO2_kg_day: CO2_kg_h * 24,
    elec_kWh_day: H2_kg_h * 5, // Minimal electricity
  };
};

const calculateElectrolyzer = (H2_tpd) => {
  if (H2_tpd === 0) return { H2_kg_day: 0, elec_kWh_day: 0, water_gal_day: 0 };
  
  const H2_kg_day = H2_tpd * 1000;
  const SEC = 53.04; // kWh/kg
  const water_kg_per_kg = 10;
  
  return {
    H2_kg_day,
    elec_kWh_day: H2_kg_day * SEC / 24 * 24,
    water_gal_day: H2_kg_day * water_kg_per_kg / 3.785,
  };
};

const calculateLH2Delivery = (throughput_tpd) => {
  if (throughput_tpd === 0) return { H2_kg_day: 0, elec_kWh_day: 0, heat_kW_day: 0 };
  
  const H2_kg_day = throughput_tpd * 1000 * 0.98; // 2% loss
  const pump_kW = 8.86 * (throughput_tpd / 0.5);
  const aux_kW = 25;
  
  return {
    H2_kg_day,
    elec_kWh_day: (pump_kW + aux_kW) * 24,
    heat_kW_day: 25.29 * 24 * (throughput_tpd / 0.5),
  };
};

const calculateH2Station = (H2_from_buffer, H2_from_trailer, demand = 1000) => {
  const total_input = H2_from_buffer + H2_from_trailer;
  if (total_input === 0) return { H2_dispensed: 0, elec_kWh_day: 0, waste_heat: 0 };
  
  const loss_frac = 0.02;
  const H2_available = total_input * (1 - loss_frac);
  const H2_dispensed = Math.min(H2_available, demand);
  
  // Compression energy
  const SEC_buffer = 8; // kWh/kg from 20 bar
  const SEC_trailer = 1.5; // kWh/kg from 250 bar
  
  const buffer_after_loss = H2_from_buffer * (1 - loss_frac);
  const trailer_after_loss = H2_from_trailer * (1 - loss_frac);
  
  const comp_energy = buffer_after_loss * SEC_buffer + trailer_after_loss * SEC_trailer;
  const chiller_energy = (H2_dispensed / 24) * 1 / 2.5 * 24; // precool / COP
  const BOP = 120;
  
  return {
    H2_dispensed,
    elec_kWh_day: comp_energy + chiller_energy + BOP,
    waste_heat: comp_energy,
  };
};

const calculateCNGStation = (trucks) => {
  if (trucks === 0) return { NG_scf_day: 0, elec_kWh_day: 0 };
  
  const fuel_per_truck = 80; // GGE
  const scf_per_GGE = 114;
  const demand_scf = trucks * fuel_per_truck * scf_per_GGE;
  const losses = demand_scf * 0.027; // ~2.7% total losses
  const comp_energy = trucks * fuel_per_truck * 0.6;
  const aux = 101.8;
  
  return {
    NG_scf_day: demand_scf + losses,
    elec_kWh_day: comp_energy + aux,
  };
};

const calculateFuelCell = (H2_kg_day) => {
  if (H2_kg_day === 0) return { elec_kWh_day: 0, heat_kWh_day: 0 };
  
  const eta_el = 0.5;
  const eta_th = 0.3;
  const LHV = 33.33;
  const BOP_frac = 0.02;
  
  const gross_elec = H2_kg_day * LHV * eta_el;
  const net_elec = gross_elec * (1 - BOP_frac);
  const heat = H2_kg_day * LHV * eta_th;
  
  return {
    elec_kWh_day: net_elec,
    heat_kWh_day: heat,
  };
};

const runScenario = (inputs) => {
  const {
    trigen_NG_scf_h = 0,
    smr_H2_tpd = 0,
    electrolyzer_H2_tpd = 0,
    lh2_tpd = 0,
    gh2_trailer_kg_day = 0,
    cng_trucks = 25,
    ev_demand_kWh = 20000,
    fuel_cell_enabled = false,
    H2_demand = 1000,
  } = inputs;

  // Run individual systems
  const trigen = calculateTriGen(trigen_NG_scf_h);
  const smr = calculateSMR(smr_H2_tpd, trigen.heat_kW_day);
  const elec = calculateElectrolyzer(electrolyzer_H2_tpd);
  const lh2 = calculateLH2Delivery(lh2_tpd);
  const cng = calculateCNGStation(cng_trucks);

  // H2 to buffer
  const H2_to_buffer = trigen.H2_kg_day + smr.H2_kg_day + elec.H2_kg_day;
  
  // Fuel cell (if enabled and excess H2)
  let H2_to_fc = 0;
  let fc = { elec_kWh_day: 0, heat_kWh_day: 0 };
  if (fuel_cell_enabled && H2_to_buffer > H2_demand) {
    H2_to_fc = H2_to_buffer - H2_demand;
    fc = calculateFuelCell(H2_to_fc);
  }

  // H2 Station
  const H2_buffer_to_station = H2_to_buffer - H2_to_fc;
  const h2s = calculateH2Station(H2_buffer_to_station, gh2_trailer_kg_day, H2_demand);

  // Total H2 dispensed (station + LH2 direct)
  const total_H2_dispensed = h2s.H2_dispensed + lh2.H2_kg_day;

  // Electricity balance
  const elec_gen = trigen.elec_kWh_day + fc.elec_kWh_day;
  const elec_con = elec.elec_kWh_day + h2s.elec_kWh_day + lh2.elec_kWh_day + cng.elec_kWh_day;
  const elec_for_ev = Math.max(0, elec_gen - elec_con);
  const grid_import = Math.max(0, ev_demand_kWh - elec_for_ev);

  // NG balance
  const total_NG = trigen.NG_scf_day + smr.NG_scf_day + cng.NG_scf_day;

  // Energy KPIs
  const NG_HHV_factor = 0.2847;
  const H2_HHV_factor = 39.4;
  const H2_LHV_factor = 33.33;

  const energy_input = total_NG * NG_HHV_factor + grid_import + 
    (lh2_tpd * 1000 + gh2_trailer_kg_day) * H2_HHV_factor;
  const energy_output = total_H2_dispensed * H2_LHV_factor + 
    cng.NG_scf_day * NG_HHV_factor + ev_demand_kWh;
  
  const LEC = energy_output > 0 ? energy_input / energy_output : 0;

  return {
    // H2 Balance
    H2_from_trigen: trigen.H2_kg_day,
    H2_from_smr: smr.H2_kg_day,
    H2_from_electrolyzer: elec.H2_kg_day,
    H2_from_lh2: lh2.H2_kg_day,
    H2_from_trailer: gh2_trailer_kg_day,
    H2_to_fc,
    H2_dispensed: total_H2_dispensed,
    
    // NG Balance
    NG_trigen: trigen.NG_scf_day,
    NG_smr: smr.NG_scf_day,
    NG_cng: cng.NG_scf_day,
    NG_total: total_NG,
    
    // Electricity
    elec_trigen: trigen.elec_kWh_day,
    elec_fc: fc.elec_kWh_day,
    elec_electrolyzer: elec.elec_kWh_day,
    elec_h2_station: h2s.elec_kWh_day,
    elec_lh2: lh2.elec_kWh_day,
    elec_cng: cng.elec_kWh_day,
    elec_ev: ev_demand_kWh,
    elec_grid: grid_import,
    elec_gen,
    elec_con: elec_con + ev_demand_kWh,
    
    // Heat
    heat_trigen: trigen.heat_kW_day,
    heat_fc: fc.heat_kWh_day,
    
    // Emissions
    CO2_smr: smr.CO2_kg_day,
    
    // KPIs
    energy_input,
    energy_output,
    LEC,
    self_sufficiency: elec_gen > 0 ? Math.min(1, elec_gen / (elec_con + ev_demand_kWh)) : 0,
  };
};

// ============================================================
// DASHBOARD COMPONENT
// ============================================================

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#06b6d4'];

export default function StationDashboard() {
  // Scenario inputs
  const [inputs, setInputs] = useState({
    trigen_NG_scf_h: 10700,
    smr_H2_tpd: 0,
    electrolyzer_H2_tpd: 0,
    lh2_tpd: 0.5,
    gh2_trailer_kg_day: 0,
    cng_trucks: 25,
    ev_demand_kWh: 20000,
    fuel_cell_enabled: false,
    H2_demand: 1000,
  });

  const [activeTab, setActiveTab] = useState('overview');
  const [compareMode, setCompareMode] = useState(false);
  const [savedScenarios, setSavedScenarios] = useState([]);

  // Calculate results
  const results = useMemo(() => runScenario(inputs), [inputs]);

  // Preset scenarios
  const presets = {
    'TriGen Only': { trigen_NG_scf_h: 10700, smr_H2_tpd: 0, electrolyzer_H2_tpd: 0, lh2_tpd: 0, gh2_trailer_kg_day: 500 },
    'TriGen + SMR': { trigen_NG_scf_h: 10700, smr_H2_tpd: 0.5, electrolyzer_H2_tpd: 0, lh2_tpd: 0, gh2_trailer_kg_day: 0 },
    'TriGen + Electrolyzer': { trigen_NG_scf_h: 10700, smr_H2_tpd: 0, electrolyzer_H2_tpd: 0.5, lh2_tpd: 0, gh2_trailer_kg_day: 0 },
    'TriGen + LH2': { trigen_NG_scf_h: 10700, smr_H2_tpd: 0, electrolyzer_H2_tpd: 0, lh2_tpd: 0.5, gh2_trailer_kg_day: 0 },
    'All Sources': { trigen_NG_scf_h: 10700, smr_H2_tpd: 0.25, electrolyzer_H2_tpd: 0.25, lh2_tpd: 0.25, gh2_trailer_kg_day: 200 },
  };

  const handleInputChange = (key, value) => {
    setInputs(prev => ({ ...prev, [key]: value }));
  };

  const loadPreset = (name) => {
    setInputs(prev => ({ ...prev, ...presets[name] }));
  };

  const saveScenario = () => {
    const name = `Scenario ${savedScenarios.length + 1}`;
    setSavedScenarios(prev => [...prev, { name, inputs: { ...inputs }, results: { ...results } }]);
  };

  // Chart data
  const h2SourcesData = [
    { name: 'TriGen', value: results.H2_from_trigen, color: '#3b82f6' },
    { name: 'SMR', value: results.H2_from_smr, color: '#10b981' },
    { name: 'Electrolyzer', value: results.H2_from_electrolyzer, color: '#f59e0b' },
    { name: 'LH2 Delivery', value: results.H2_from_lh2, color: '#8b5cf6' },
    { name: 'GH2 Trailer', value: results.H2_from_trailer, color: '#06b6d4' },
  ].filter(d => d.value > 0);

  const ngUsageData = [
    { name: 'TriGen', value: results.NG_trigen },
    { name: 'SMR', value: results.NG_smr },
    { name: 'CNG Station', value: results.NG_cng },
  ].filter(d => d.value > 0);

  const elecBalanceData = [
    { name: 'Generation', TriGen: results.elec_trigen, 'Fuel Cell': results.elec_fc },
    { name: 'Consumption', Electrolyzer: -results.elec_electrolyzer, 'H2 Station': -results.elec_h2_station, 
      'LH2 System': -results.elec_lh2, 'CNG Station': -results.elec_cng, 'EV Charger': -results.elec_ev },
  ];

  const elecFlowData = [
    { name: 'TriGen', value: results.elec_trigen, type: 'gen' },
    { name: 'Fuel Cell', value: results.elec_fc, type: 'gen' },
    { name: 'Grid', value: results.elec_grid, type: 'gen' },
    { name: 'Electrolyzer', value: results.elec_electrolyzer, type: 'con' },
    { name: 'H2 Station', value: results.elec_h2_station, type: 'con' },
    { name: 'LH2 Pump', value: results.elec_lh2, type: 'con' },
    { name: 'CNG Station', value: results.elec_cng, type: 'con' },
    { name: 'EV Charger', value: results.elec_ev, type: 'con' },
  ];

  const compareData = savedScenarios.map(s => ({
    name: s.name,
    LEC: s.results.LEC,
    H2: s.results.H2_dispensed,
    Grid: s.results.elec_grid / 1000,
  }));

  // Add current scenario to compare
  if (compareMode) {
    compareData.push({
      name: 'Current',
      LEC: results.LEC,
      H2: results.H2_dispensed,
      Grid: results.elec_grid / 1000,
    });
  }

  return (
    <div className="min-h-screen bg-gray-900 text-white p-4">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-6">
          <h1 className="text-3xl font-bold text-blue-400 mb-2">Station of the Future</h1>
          <p className="text-gray-400">Mass & Energy Balance Dashboard</p>
        </div>

        {/* Tabs */}
        <div className="flex gap-2 mb-6">
          {['overview', 'hydrogen', 'electricity', 'compare'].map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2 rounded-lg font-medium transition ${
                activeTab === tab 
                  ? 'bg-blue-600 text-white' 
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
              }`}
            >
              {tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>

        <div className="grid grid-cols-12 gap-4">
          {/* Left Panel - Inputs */}
          <div className="col-span-3 bg-gray-800 rounded-xl p-4">
            <h2 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <Factory className="w-5 h-5 text-blue-400" />
              Configuration
            </h2>

            {/* Presets */}
            <div className="mb-4">
              <label className="text-sm text-gray-400 mb-2 block">Load Preset</label>
              <select
                className="w-full bg-gray-700 rounded-lg p-2 text-sm"
                onChange={(e) => e.target.value && loadPreset(e.target.value)}
                defaultValue=""
              >
                <option value="">Select preset...</option>
                {Object.keys(presets).map(name => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </div>

            <div className="space-y-4">
              {/* TriGen */}
              <div>
                <label className="text-sm text-gray-400 flex items-center gap-2">
                  <Flame className="w-4 h-4 text-orange-400" />
                  TriGen NG (scf/h)
                </label>
                <input
                  type="range"
                  min="0"
                  max="21840"
                  step="100"
                  value={inputs.trigen_NG_scf_h}
                  onChange={(e) => handleInputChange('trigen_NG_scf_h', Number(e.target.value))}
                  className="w-full mt-1"
                />
                <span className="text-sm text-blue-400">{inputs.trigen_NG_scf_h.toLocaleString()}</span>
              </div>

              {/* SMR */}
              <div>
                <label className="text-sm text-gray-400 flex items-center gap-2">
                  <Factory className="w-4 h-4 text-green-400" />
                  SMR H2 (tpd)
                </label>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  value={inputs.smr_H2_tpd}
                  onChange={(e) => handleInputChange('smr_H2_tpd', Number(e.target.value))}
                  className="w-full mt-1"
                />
                <span className="text-sm text-blue-400">{inputs.smr_H2_tpd.toFixed(1)}</span>
              </div>

              {/* Electrolyzer */}
              <div>
                <label className="text-sm text-gray-400 flex items-center gap-2">
                  <Zap className="w-4 h-4 text-yellow-400" />
                  Electrolyzer H2 (tpd)
                </label>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  value={inputs.electrolyzer_H2_tpd}
                  onChange={(e) => handleInputChange('electrolyzer_H2_tpd', Number(e.target.value))}
                  className="w-full mt-1"
                />
                <span className="text-sm text-blue-400">{inputs.electrolyzer_H2_tpd.toFixed(1)}</span>
              </div>

              {/* LH2 */}
              <div>
                <label className="text-sm text-gray-400 flex items-center gap-2">
                  <Droplets className="w-4 h-4 text-cyan-400" />
                  LH2 Delivery (tpd)
                </label>
                <input
                  type="range"
                  min="0"
                  max="2"
                  step="0.1"
                  value={inputs.lh2_tpd}
                  onChange={(e) => handleInputChange('lh2_tpd', Number(e.target.value))}
                  className="w-full mt-1"
                />
                <span className="text-sm text-blue-400">{inputs.lh2_tpd.toFixed(1)}</span>
              </div>

              {/* GH2 Trailer */}
              <div>
                <label className="text-sm text-gray-400 flex items-center gap-2">
                  <Truck className="w-4 h-4 text-purple-400" />
                  GH2 Trailer (kg/day)
                </label>
                <input
                  type="range"
                  min="0"
                  max="1000"
                  step="50"
                  value={inputs.gh2_trailer_kg_day}
                  onChange={(e) => handleInputChange('gh2_trailer_kg_day', Number(e.target.value))}
                  className="w-full mt-1"
                />
                <span className="text-sm text-blue-400">{inputs.gh2_trailer_kg_day}</span>
              </div>

              {/* CNG Trucks */}
              <div>
                <label className="text-sm text-gray-400 flex items-center gap-2">
                  <Truck className="w-4 h-4 text-amber-400" />
                  CNG Trucks/day
                </label>
                <input
                  type="range"
                  min="0"
                  max="50"
                  step="1"
                  value={inputs.cng_trucks}
                  onChange={(e) => handleInputChange('cng_trucks', Number(e.target.value))}
                  className="w-full mt-1"
                />
                <span className="text-sm text-blue-400">{inputs.cng_trucks}</span>
              </div>

              {/* EV Demand */}
              <div>
                <label className="text-sm text-gray-400 flex items-center gap-2">
                  <Car className="w-4 h-4 text-green-400" />
                  EV Demand (kWh/day)
                </label>
                <input
                  type="range"
                  min="0"
                  max="50000"
                  step="1000"
                  value={inputs.ev_demand_kWh}
                  onChange={(e) => handleInputChange('ev_demand_kWh', Number(e.target.value))}
                  className="w-full mt-1"
                />
                <span className="text-sm text-blue-400">{(inputs.ev_demand_kWh/1000).toFixed(0)} MWh</span>
              </div>

              {/* Fuel Cell Toggle */}
              <div className="flex items-center justify-between">
                <label className="text-sm text-gray-400 flex items-center gap-2">
                  <Battery className="w-4 h-4 text-blue-400" />
                  Fuel Cell
                </label>
                <button
                  onClick={() => handleInputChange('fuel_cell_enabled', !inputs.fuel_cell_enabled)}
                  className={`w-12 h-6 rounded-full transition ${
                    inputs.fuel_cell_enabled ? 'bg-blue-600' : 'bg-gray-600'
                  }`}
                >
                  <div className={`w-5 h-5 bg-white rounded-full transition transform ${
                    inputs.fuel_cell_enabled ? 'translate-x-6' : 'translate-x-0.5'
                  }`} />
                </button>
              </div>
            </div>

            {/* Save Button */}
            <button
              onClick={saveScenario}
              className="w-full mt-4 bg-blue-600 hover:bg-blue-700 text-white py-2 rounded-lg font-medium transition"
            >
              Save Scenario
            </button>
          </div>

          {/* Main Content */}
          <div className="col-span-9">
            {activeTab === 'overview' && (
              <div className="space-y-4">
                {/* KPI Cards */}
                <div className="grid grid-cols-4 gap-4">
                  <div className="bg-gray-800 rounded-xl p-4">
                    <div className="flex items-center gap-2 text-gray-400 text-sm mb-1">
                      <Fuel className="w-4 h-4" />
                      H2 Dispensed
                    </div>
                    <div className="text-2xl font-bold text-blue-400">
                      {results.H2_dispensed.toLocaleString(undefined, {maximumFractionDigits: 0})}
                    </div>
                    <div className="text-sm text-gray-500">kg/day</div>
                  </div>

                  <div className="bg-gray-800 rounded-xl p-4">
                    <div className="flex items-center gap-2 text-gray-400 text-sm mb-1">
                      <Flame className="w-4 h-4" />
                      Total NG
                    </div>
                    <div className="text-2xl font-bold text-orange-400">
                      {(results.NG_total/1000).toLocaleString(undefined, {maximumFractionDigits: 0})}k
                    </div>
                    <div className="text-sm text-gray-500">scf/day</div>
                  </div>

                  <div className="bg-gray-800 rounded-xl p-4">
                    <div className="flex items-center gap-2 text-gray-400 text-sm mb-1">
                      <Zap className="w-4 h-4" />
                      Grid Import
                    </div>
                    <div className={`text-2xl font-bold ${results.elec_grid > 0 ? 'text-red-400' : 'text-green-400'}`}>
                      {(results.elec_grid/1000).toLocaleString(undefined, {maximumFractionDigits: 1})}
                    </div>
                    <div className="text-sm text-gray-500">MWh/day</div>
                  </div>

                  <div className="bg-gray-800 rounded-xl p-4">
                    <div className="flex items-center gap-2 text-gray-400 text-sm mb-1">
                      {results.LEC < 1.5 ? <CheckCircle className="w-4 h-4 text-green-400" /> : <AlertTriangle className="w-4 h-4 text-yellow-400" />}
                      LEC
                    </div>
                    <div className={`text-2xl font-bold ${results.LEC < 1.5 ? 'text-green-400' : 'text-yellow-400'}`}>
                      {results.LEC.toFixed(3)}
                    </div>
                    <div className="text-sm text-gray-500">{results.LEC < 1.5 ? 'Good' : 'Review'}</div>
                  </div>
                </div>

                {/* Charts Row */}
                <div className="grid grid-cols-2 gap-4">
                  {/* H2 Sources Pie */}
                  <div className="bg-gray-800 rounded-xl p-4">
                    <h3 className="text-lg font-semibold mb-4">H2 Sources</h3>
                    <ResponsiveContainer width="100%" height={250}>
                      <PieChart>
                        <Pie
                          data={h2SourcesData}
                          cx="50%"
                          cy="50%"
                          innerRadius={60}
                          outerRadius={100}
                          paddingAngle={2}
                          dataKey="value"
                          label={({name, value}) => `${name}: ${value.toFixed(0)}`}
                        >
                          {h2SourcesData.map((entry, index) => (
                            <Cell key={index} fill={entry.color} />
                          ))}
                        </Pie>
                        <Tooltip formatter={(v) => `${v.toFixed(0)} kg/day`} />
                      </PieChart>
                    </ResponsiveContainer>
                  </div>

                  {/* NG Usage Bar */}
                  <div className="bg-gray-800 rounded-xl p-4">
                    <h3 className="text-lg font-semibold mb-4">Natural Gas Usage</h3>
                    <ResponsiveContainer width="100%" height={250}>
                      <BarChart data={ngUsageData} layout="vertical">
                        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                        <XAxis type="number" tickFormatter={(v) => `${(v/1000).toFixed(0)}k`} />
                        <YAxis type="category" dataKey="name" width={80} />
                        <Tooltip formatter={(v) => `${v.toLocaleString()} scf/day`} />
                        <Bar dataKey="value" fill="#f59e0b" radius={4} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                {/* Energy Balance */}
                <div className="bg-gray-800 rounded-xl p-4">
                  <h3 className="text-lg font-semibold mb-4">Energy Balance</h3>
                  <div className="grid grid-cols-3 gap-8">
                    <div>
                      <h4 className="text-sm text-gray-400 mb-2">Generation</h4>
                      <div className="space-y-2">
                        <div className="flex justify-between">
                          <span>TriGen</span>
                          <span className="text-green-400">{(results.elec_trigen/1000).toFixed(1)} MWh</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Fuel Cell</span>
                          <span className="text-green-400">{(results.elec_fc/1000).toFixed(1)} MWh</span>
                        </div>
                        <div className="flex justify-between">
                          <span>Grid Import</span>
                          <span className="text-red-400">{(results.elec_grid/1000).toFixed(1)} MWh</span>
                        </div>
                        <div className="border-t border-gray-700 pt-2 flex justify-between font-semibold">
                          <span>Total</span>
                          <span className="text-blue-400">{((results.elec_gen + results.elec_grid)/1000).toFixed(1)} MWh</span>
                        </div>
                      </div>
                    </div>
                    <div>
                      <h4 className="text-sm text-gray-400 mb-2">Consumption</h4>
                      <div className="space-y-2">
                        <div className="flex justify-between">
                          <span>Electrolyzer</span>
                          <span className="text-yellow-400">{(results.elec_electrolyzer/1000).toFixed(1)} MWh</span>
                        </div>
                        <div className="flex justify-between">
                          <span>H2 Station</span>
                          <span className="text-yellow-400">{(results.elec_h2_station/1000).toFixed(1)} MWh</span>
                        </div>
                        <div className="flex justify-between">
                          <span>LH2 System</span>
                          <span className="text-yellow-400">{(results.elec_lh2/1000).toFixed(1)} MWh</span>
                        </div>
                        <div className="flex justify-between">
                          <span>CNG Station</span>
                          <span className="text-yellow-400">{(results.elec_cng/1000).toFixed(1)} MWh</span>
                        </div>
                        <div className="flex justify-between">
                          <span>EV Charger</span>
                          <span className="text-yellow-400">{(results.elec_ev/1000).toFixed(1)} MWh</span>
                        </div>
                        <div className="border-t border-gray-700 pt-2 flex justify-between font-semibold">
                          <span>Total</span>
                          <span className="text-blue-400">{(results.elec_con/1000).toFixed(1)} MWh</span>
                        </div>
                      </div>
                    </div>
                    <div>
                      <h4 className="text-sm text-gray-400 mb-2">Metrics</h4>
                      <div className="space-y-4">
                        <div>
                          <div className="text-sm text-gray-400">Self-Sufficiency</div>
                          <div className="w-full bg-gray-700 rounded-full h-4 mt-1">
                            <div 
                              className="bg-green-500 h-4 rounded-full transition-all"
                              style={{ width: `${results.self_sufficiency * 100}%` }}
                            />
                          </div>
                          <div className="text-right text-sm mt-1">{(results.self_sufficiency * 100).toFixed(0)}%</div>
                        </div>
                        <div>
                          <div className="text-sm text-gray-400">CO2 from SMR</div>
                          <div className="text-xl font-semibold text-orange-400">{results.CO2_smr.toFixed(0)} kg/day</div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'hydrogen' && (
              <div className="space-y-4">
                <div className="bg-gray-800 rounded-xl p-4">
                  <h3 className="text-lg font-semibold mb-4">Hydrogen Flow Diagram</h3>
                  <div className="grid grid-cols-3 gap-4 items-center">
                    {/* Sources */}
                    <div className="space-y-2">
                      <h4 className="text-sm text-gray-400 text-center mb-4">Production Sources</h4>
                      {[
                        { name: 'TriGen', value: results.H2_from_trigen, color: 'blue' },
                        { name: 'SMR', value: results.H2_from_smr, color: 'green' },
                        { name: 'Electrolyzer', value: results.H2_from_electrolyzer, color: 'yellow' },
                        { name: 'LH2 Delivery', value: results.H2_from_lh2, color: 'cyan' },
                        { name: 'GH2 Trailer', value: results.H2_from_trailer, color: 'purple' },
                      ].map(source => (
                        <div key={source.name} className={`bg-gray-700 rounded-lg p-3 border-l-4 border-${source.color}-500`}>
                          <div className="text-sm text-gray-400">{source.name}</div>
                          <div className="text-xl font-bold">{source.value.toFixed(0)} kg/day</div>
                        </div>
                      ))}
                    </div>

                    {/* Flow Arrows */}
                    <div className="flex flex-col items-center justify-center">
                      <div className="text-4xl text-gray-600">→</div>
                      <div className="bg-gray-700 rounded-lg p-4 my-4">
                        <div className="text-center text-sm text-gray-400">LP Buffer</div>
                        <div className="text-2xl font-bold text-blue-400 text-center">
                          {(results.H2_from_trigen + results.H2_from_smr + results.H2_from_electrolyzer).toFixed(0)}
                        </div>
                        <div className="text-center text-sm text-gray-500">kg/day</div>
                      </div>
                      <div className="text-4xl text-gray-600">→</div>
                    </div>

                    {/* Sinks */}
                    <div className="space-y-2">
                      <h4 className="text-sm text-gray-400 text-center mb-4">Consumption</h4>
                      <div className="bg-gray-700 rounded-lg p-3 border-l-4 border-green-500">
                        <div className="text-sm text-gray-400">Dispensed</div>
                        <div className="text-xl font-bold text-green-400">{results.H2_dispensed.toFixed(0)} kg/day</div>
                      </div>
                      <div className="bg-gray-700 rounded-lg p-3 border-l-4 border-orange-500">
                        <div className="text-sm text-gray-400">To Fuel Cell</div>
                        <div className="text-xl font-bold text-orange-400">{results.H2_to_fc.toFixed(0)} kg/day</div>
                      </div>
                      <div className="bg-gray-700 rounded-lg p-3 border-l-4 border-red-500">
                        <div className="text-sm text-gray-400">Demand Target</div>
                        <div className="text-xl font-bold">{inputs.H2_demand} kg/day</div>
                      </div>
                    </div>
                  </div>
                </div>

                {/* H2 Production Breakdown Chart */}
                <div className="bg-gray-800 rounded-xl p-4">
                  <h3 className="text-lg font-semibold mb-4">Production Breakdown</h3>
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart data={[
                      { name: 'TriGen', H2: results.H2_from_trigen, NG: results.NG_trigen / 1000, Elec: 0 },
                      { name: 'SMR', H2: results.H2_from_smr, NG: results.NG_smr / 1000, Elec: 0 },
                      { name: 'Electrolyzer', H2: results.H2_from_electrolyzer, NG: 0, Elec: results.elec_electrolyzer / 1000 },
                      { name: 'LH2', H2: results.H2_from_lh2, NG: 0, Elec: results.elec_lh2 / 1000 },
                      { name: 'GH2 Trailer', H2: results.H2_from_trailer, NG: 0, Elec: 0 },
                    ]}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                      <XAxis dataKey="name" />
                      <YAxis yAxisId="left" orientation="left" label={{ value: 'H2 (kg/day)', angle: -90, position: 'insideLeft' }} />
                      <YAxis yAxisId="right" orientation="right" label={{ value: 'Input (k scf or MWh)', angle: 90, position: 'insideRight' }} />
                      <Tooltip />
                      <Legend />
                      <Bar yAxisId="left" dataKey="H2" fill="#3b82f6" name="H2 Output (kg/day)" />
                      <Bar yAxisId="right" dataKey="NG" fill="#f59e0b" name="NG Input (k scf/day)" />
                      <Bar yAxisId="right" dataKey="Elec" fill="#10b981" name="Elec Input (MWh/day)" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>
              </div>
            )}

            {activeTab === 'electricity' && (
              <div className="space-y-4">
                {/* Generation vs Consumption */}
                <div className="bg-gray-800 rounded-xl p-4">
                  <h3 className="text-lg font-semibold mb-4">Electricity Flow</h3>
                  <ResponsiveContainer width="100%" height={350}>
                    <BarChart data={elecFlowData} layout="vertical">
                      <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                      <XAxis type="number" tickFormatter={(v) => `${(v/1000).toFixed(1)}`} label={{ value: 'MWh/day', position: 'bottom' }} />
                      <YAxis type="category" dataKey="name" width={100} />
                      <Tooltip formatter={(v) => `${(v/1000).toFixed(2)} MWh/day`} />
                      <Bar dataKey="value" radius={4}>
                        {elecFlowData.map((entry, index) => (
                          <Cell key={index} fill={entry.type === 'gen' ? '#10b981' : '#ef4444'} />
                        ))}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                {/* Power Balance Summary */}
                <div className="grid grid-cols-2 gap-4">
                  <div className="bg-gray-800 rounded-xl p-4">
                    <h3 className="text-lg font-semibold mb-4 text-green-400">Generation</h3>
                    <div className="text-4xl font-bold mb-2">{(results.elec_gen/1000).toFixed(1)} MWh/day</div>
                    <div className="space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span>TriGen</span>
                        <span>{((results.elec_trigen/results.elec_gen)*100 || 0).toFixed(0)}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Fuel Cell</span>
                        <span>{((results.elec_fc/results.elec_gen)*100 || 0).toFixed(0)}%</span>
                      </div>
                    </div>
                  </div>

                  <div className="bg-gray-800 rounded-xl p-4">
                    <h3 className="text-lg font-semibold mb-4 text-red-400">Consumption</h3>
                    <div className="text-4xl font-bold mb-2">{(results.elec_con/1000).toFixed(1)} MWh/day</div>
                    <div className="space-y-2 text-sm">
                      <div className="flex justify-between">
                        <span>EV Charger</span>
                        <span>{((results.elec_ev/results.elec_con)*100).toFixed(0)}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span>H2 Station</span>
                        <span>{((results.elec_h2_station/results.elec_con)*100).toFixed(0)}%</span>
                      </div>
                      <div className="flex justify-between">
                        <span>Other</span>
                        <span>{(((results.elec_electrolyzer + results.elec_lh2 + results.elec_cng)/results.elec_con)*100).toFixed(0)}%</span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {activeTab === 'compare' && (
              <div className="space-y-4">
                <div className="bg-gray-800 rounded-xl p-4">
                  <div className="flex justify-between items-center mb-4">
                    <h3 className="text-lg font-semibold">Scenario Comparison</h3>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={compareMode}
                        onChange={(e) => setCompareMode(e.target.checked)}
                        className="rounded"
                      />
                      Include Current
                    </label>
                  </div>
                  
                  {compareData.length > 0 ? (
                    <ResponsiveContainer width="100%" height={400}>
                      <BarChart data={compareData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
                        <XAxis dataKey="name" />
                        <YAxis yAxisId="left" orientation="left" />
                        <YAxis yAxisId="right" orientation="right" />
                        <Tooltip />
                        <Legend />
                        <Bar yAxisId="left" dataKey="LEC" fill="#3b82f6" name="LEC" />
                        <Bar yAxisId="left" dataKey="H2" fill="#10b981" name="H2 Dispensed (kg)" />
                        <Bar yAxisId="right" dataKey="Grid" fill="#ef4444" name="Grid Import (MWh)" />
                      </BarChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="text-center text-gray-400 py-12">
                      Save some scenarios to compare them here
                    </div>
                  )}
                </div>

                {/* Saved Scenarios Table */}
                {savedScenarios.length > 0 && (
                  <div className="bg-gray-800 rounded-xl p-4">
                    <h3 className="text-lg font-semibold mb-4">Saved Scenarios</h3>
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="text-gray-400 border-b border-gray-700">
                          <th className="text-left py-2">Name</th>
                          <th className="text-right py-2">H2 (kg/day)</th>
                          <th className="text-right py-2">NG (k scf/day)</th>
                          <th className="text-right py-2">Grid (MWh)</th>
                          <th className="text-right py-2">LEC</th>
                        </tr>
                      </thead>
                      <tbody>
                        {savedScenarios.map((s, i) => (
                          <tr key={i} className="border-b border-gray-700">
                            <td className="py-2">{s.name}</td>
                            <td className="text-right">{s.results.H2_dispensed.toFixed(0)}</td>
                            <td className="text-right">{(s.results.NG_total/1000).toFixed(0)}</td>
                            <td className="text-right">{(s.results.elec_grid/1000).toFixed(1)}</td>
                            <td className={`text-right ${s.results.LEC < 1.5 ? 'text-green-400' : 'text-yellow-400'}`}>
                              {s.results.LEC.toFixed(3)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
