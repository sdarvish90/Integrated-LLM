import React, { useState } from 'react';

const DecarbIQRelationshipGraph = () => {
  const [selectedNode, setSelectedNode] = useState(null);

  const nodeData = {
    supply: {
      title: "SUPPLY",
      color: "#3498db",
      items: [
        { name: "Shale Production", coef: -1.30, var: "shale_era" },
        { name: "LNG Exports", coef: +0.12, var: "lng_exports_bcfd" },
        { name: "Drilling Activity", coef: -0.003, var: "gas_rigs" },
        { name: "Net Export Status", coef: -1.48, var: "us_net_exporter" }
      ]
    },
    demand: {
      title: "DEMAND",
      color: "#2ecc71",
      items: [
        { name: "GDP Growth", coef: +0.486, var: "us_gdp_growth_pct", sig: "***" },
        { name: "Industrial Prod", coef: +0.118, var: "us_industrial_prod_index", sig: "**" },
        { name: "Power Sector", coef: -0.064, var: "electric_power_bcfd", sig: "*" },
        { name: "Seasonal (Summer)", coef: +0.60, var: "is_summer", sig: "*" }
      ]
    },
    dataCenter: {
      title: "DATA CENTER DEMAND",
      color: "#e91e63",
      items: [
        { name: "US DC Load", coef: +0.059, var: "us_data_center_twh", sig: "**" },
        { name: "TX DC Load", value: "55->200 TWh", var: "tx_data_center_twh" },
        { name: "ERCOT Queue", value: "230+ GW", var: "ercot_large_load_queue_gw" }
      ]
    },
    policy: {
      title: "POLICY",
      color: "#9b59b6",
      items: [
        { name: "IRA->OBBBA", coef: +8.57, date: "2022->2025", sig: "***" },
        { name: "FERC Reforms (12)", coef: +0.57, var: "cumulative_ferc_reforms", sig: "*" },
        { name: "TX CREZ", value: "$7B->18.5 GW", date: "2005-2014" },
        { name: "MATS Rule", coef: +0.86, date: "2015" }
      ]
    },
    queue: {
      title: "QUEUE CONSTRAINT",
      color: "#ff9800",
      items: [
        { name: "National Backlog", value: "2,600 GW" },
        { name: "Avg Wait", value: "5 years" },
        { name: "Solar Completion", value: "14%" }
      ]
    },
    genMix: {
      title: "GENERATION MIX (TX)",
      color: "#f1c40f",
      items: [
        { name: "Gas Share", value: "47%", corr: +0.81 },
        { name: "Wind Share", value: "25% (40+ GW)" },
        { name: "Coal Share", value: "16% (declining)" },
        { name: "Solar Share", value: "8% (rising)" }
      ]
    },
    electricity: {
      title: "ERCOT ELECTRICITY",
      color: "#e67e22",
      regions: {
        ERCOT: { passthrough: 41.33, r2: 0.149, n: 169, driver: "Gas Price" },
        CAISO: { passthrough: 14.19, r2: 0.569, n: 183, driver: "Policy + Gas" }
      }
    }
  };

  const NodeBox = ({ id, data, x, y, width = 200 }) => (
    <g
      transform={`translate(${x}, ${y})`}
      onClick={() => setSelectedNode(selectedNode === id ? null : id)}
      style={{ cursor: 'pointer' }}
    >
      <rect
        width={width}
        height={data.items ? 30 + data.items.length * 25 : 80}
        rx="8"
        fill={data.color}
        opacity={selectedNode && selectedNode !== id ? 0.5 : 1}
        stroke={selectedNode === id ? "#fff" : "none"}
        strokeWidth="3"
      />
      <text x={width/2} y="22" textAnchor="middle" fill="white" fontWeight="bold" fontSize="14">
        {data.title}
      </text>
      {data.items && data.items.map((item, i) => (
        <text key={i} x="10" y={45 + i * 22} fill="white" fontSize="11">
          {item.name}: {item.coef !== undefined ? (item.coef > 0 ? '+' : '') + item.coef.toFixed(3) : item.value || item.corr}
          {item.sig ? ` ${item.sig}` : ''}
        </text>
      ))}
    </g>
  );

  const Arrow = ({ x1, y1, x2, y2, label, dashed = false, thick = false }) => (
    <g>
      <defs>
        <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#666" />
        </marker>
        <marker id="arrowhead-red" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#e74c3c" />
        </marker>
      </defs>
      <line
        x1={x1} y1={y1} x2={x2} y2={y2}
        stroke={thick ? "#e74c3c" : "#666"}
        strokeWidth={thick ? 4 : 2}
        strokeDasharray={dashed ? "5,5" : "none"}
        markerEnd={thick ? "url(#arrowhead-red)" : "url(#arrowhead)"}
      />
      {label && (
        <text x={(x1+x2)/2} y={(y1+y2)/2 - 5} textAnchor="middle" fontSize="10" fill={thick ? "#e74c3c" : "#666"}>
          {label}
        </text>
      )}
    </g>
  );

  return (
    <div className="w-full bg-gray-900 rounded-lg p-4">
      <h2 className="text-white text-xl font-bold text-center mb-2">
        DecarbIQ ERCOT Electricity Price Relationship Model v2.0
      </h2>
      <p className="text-gray-400 text-center text-sm mb-4">
        High-impact factors only | Coefficients from regression (HH Full R&sup2;=0.612, ERCOT Full R&sup2;=0.149) | *** p&lt;0.001, ** p&lt;0.01, * p&lt;0.05
      </p>

      <svg viewBox="0 0 900 750" className="w-full">
        <rect width="900" height="750" fill="#1a1a2e" />

        {/* Row 1: Supply + Demand + Data Centers */}
        <text x="120" y="25" fill="#888" fontSize="12" textAnchor="middle">UPSTREAM</text>
        <NodeBox id="supply" data={nodeData.supply} x={10} y={35} />
        <NodeBox id="demand" data={nodeData.demand} x={230} y={35} />
        <NodeBox id="dataCenter" data={nodeData.dataCenter} x={450} y={35} width={210} />

        {/* Row 2: Policy + Queue */}
        <NodeBox id="policy" data={nodeData.policy} x={680} y={35} width={210} />
        <NodeBox id="queue" data={nodeData.queue} x={680} y={200} width={210} />

        {/* Arrows: Supply -> Gas */}
        <Arrow x1={110} y1={170} x2={350} y2={305} />

        {/* Arrows: Demand -> Gas (regression-validated) */}
        <Arrow x1={330} y1={170} x2={380} y2={305} label="+$0.49/1%GDP" />

        {/* Arrows: Data Center -> Gas + ERCOT */}
        <Arrow x1={500} y1={135} x2={420} y2={305} label="+$0.06/TWh" />
        <Arrow x1={555} y1={135} x2={500} y2={485} label="demand pull" />

        {/* Arrows: Policy -> Gas (IRA/OBBBA dominant) */}
        <Arrow x1={680} y1={100} x2={440} y2={310} label="+$8.57***" thick={true} />

        {/* Arrows: FERC -> Gas */}
        <Arrow x1={680} y1={130} x2={445} y2={330} label="+$0.57*" />

        {/* Arrows: Queue -> ERCOT */}
        <Arrow x1={680} y1={270} x2={550} y2={490} label="delays capacity" dashed={true} />

        {/* Natural Gas Price - Central Node */}
        <g transform="translate(300, 310)">
          <rect width="200" height="90" rx="10" fill="#e74c3c" stroke="#c0392b" strokeWidth="3" />
          <text x="100" y="25" textAnchor="middle" fill="white" fontWeight="bold" fontSize="16">
            NATURAL GAS PRICE
          </text>
          <text x="100" y="45" textAnchor="middle" fill="white" fontSize="12">
            Henry Hub $/MMBtu
          </text>
          <text x="100" y="65" textAnchor="middle" fill="white" fontSize="11">
            HH Full: R&sup2;=0.612 | n=336
          </text>
          <text x="100" y="82" textAnchor="middle" fill="white" fontSize="10">
            7 significant drivers (p&lt;0.05)
          </text>
        </g>

        {/* Arrow: Gas -> Gen Mix */}
        <Arrow x1={400} y1={400} x2={400} y2={430} label="passthrough" />

        {/* Generation Mix */}
        <NodeBox id="genMix" data={nodeData.genMix} x={300} y={440} />

        {/* Arrow: Gen Mix -> ERCOT */}
        <Arrow x1={400} y1={560} x2={400} y2={590} />

        {/* ERCOT Electricity Price */}
        <g transform="translate(150, 600)">
          <rect width="500" height="130" rx="10" fill="#e67e22" />
          <text x="250" y="22" textAnchor="middle" fill="white" fontWeight="bold" fontSize="14">
            ELECTRICITY PRICE
          </text>

          {/* ERCOT Box */}
          <rect x="15" y="35" width="230" height="85" rx="5" fill="#d35400" />
          <text x="130" y="52" textAnchor="middle" fill="white" fontWeight="bold" fontSize="13">ERCOT</text>
          <text x="130" y="68" textAnchor="middle" fill="white" fontSize="10">
            $41/MWh per $1/MMBtu gas (p=0.004)
          </text>
          <text x="130" y="82" textAnchor="middle" fill="white" fontSize="10">
            R&sup2;=0.149 | n=169 | Energy-only
          </text>
          <text x="130" y="96" textAnchor="middle" fill="#ffcc80" fontSize="10">
            Gas price is ONLY significant driver
          </text>
          <text x="130" y="110" textAnchor="middle" fill="#ffcc80" fontSize="9">
            DC load: 55 TWh (11.6%) | Queue: 230+ GW
          </text>

          {/* CAISO Box */}
          <rect x="260" y="35" width="225" height="85" rx="5" fill="#795548" />
          <text x="373" y="52" textAnchor="middle" fill="white" fontWeight="bold" fontSize="13">CAISO</text>
          <text x="373" y="68" textAnchor="middle" fill="white" fontSize="10">
            $14/MWh per $1/MMBtu gas (p&lt;0.001)
          </text>
          <text x="373" y="82" textAnchor="middle" fill="white" fontSize="10">
            R&sup2;=0.569 | n=183 | Regulated
          </text>
          <text x="373" y="96" textAnchor="middle" fill="#bcaaa4" fontSize="10">
            4 significant policy variables
          </text>
          <text x="373" y="110" textAnchor="middle" fill="#bcaaa4" fontSize="9">
            RPS, Cap-Trade, Gas%, Wind%
          </text>
        </g>

        {/* Key Insights Box */}
        <g transform="translate(10, 440)">
          <rect width="270" height="140" rx="5" fill="#2a2a4a" />
          <text x="135" y="18" textAnchor="middle" fill="white" fontWeight="bold" fontSize="12">ERCOT HIGH-IMPACT FACTORS</text>
          <text x="10" y="36" fill="#e74c3c" fontSize="9">1. Gas Price: $41/MWh per $1 gas (p=0.004)</text>
          <text x="10" y="52" fill="#e91e63" fontSize="9">{"2. Data Centers: 55→200 TWh, 230 GW queue"}</text>
          <text x="10" y="68" fill="#9b59b6" fontSize="9">{"3. IRA→OBBBA: +$8.57/MMBtu (p<0.001)"}</text>
          <text x="10" y="84" fill="#ff9800" fontSize="9">4. Queue Backlog: 2,600 GW, 5yr wait</text>
          <text x="10" y="100" fill="#f1c40f" fontSize="9">5. Wind: 25% share, off-peak compression</text>
          <text x="10" y="116" fill="#2ecc71" fontSize="9">6. GDP Growth: +$0.49/MMBtu per 1%</text>
          <text x="10" y="132" fill="#9b59b6" fontSize="9">7. FERC Reforms: +$0.57/MMBtu (p=0.023)</text>
        </g>
      </svg>

      {selectedNode && (
        <div className="mt-4 p-4 bg-gray-800 rounded-lg">
          <h3 className="text-white font-bold">{nodeData[selectedNode]?.title} Details</h3>
          <div className="text-gray-300 text-sm mt-2">
            {nodeData[selectedNode]?.items?.map((item, i) => (
              <div key={i} className="flex justify-between py-1 border-b border-gray-700">
                <span>{item.name} {item.sig && <span className="text-yellow-400">{item.sig}</span>}</span>
                <span className={item.coef > 0 ? "text-green-400" : item.coef < 0 ? "text-red-400" : "text-gray-400"}>
                  {item.coef !== undefined ? (item.coef > 0 ? '+' : '') + item.coef.toFixed(3) : item.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

export default DecarbIQRelationshipGraph;
