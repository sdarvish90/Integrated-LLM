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
        { name: "Power Sector", coef: +0.28, var: "electric_power_bcfd" },
        { name: "Industrial", coef: -0.43, var: "industrial_bcfd" },
        { name: "Seasonal Heating", coef: +0.15, var: "heating_demand" }
      ]
    },
    policy: {
      title: "POLICY",
      color: "#9b59b6",
      items: [
        { name: "RGGI", coef: -1.30, date: "2009" },
        { name: "MATS Rule", coef: +0.86, date: "2015" },
        { name: "IRA", coef: -2.40, date: "2022" },
        { name: "Permitting", coef: +1.52, var: "certificate_policy" }
      ]
    },
    genMix: {
      title: "GENERATION MIX",
      color: "#f1c40f",
      items: [
        { name: "Gas Share", value: "43%", corr: +0.81 },
        { name: "Coal Share", value: "16%", corr: -0.72 },
        { name: "Renewables", value: "growing", corr: +0.59 }
      ]
    },
    electricity: {
      title: "ELECTRICITY PRICE",
      color: "#e67e22",
      regions: {
        TX: { industrial: +0.68, wholesale: +0.21, mean: "6.2¢", driver: "Gas Price" },
        CA: { industrial: -0.35, wholesale: +0.42, mean: "12.0¢", driver: "Policy" }
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
          {item.name}: {item.coef !== undefined ? (item.coef > 0 ? '+' : '') + item.coef.toFixed(2) : item.value || item.corr}
        </text>
      ))}
    </g>
  );

  const Arrow = ({ x1, y1, x2, y2, label, dashed = false }) => (
    <g>
      <defs>
        <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto">
          <polygon points="0 0, 10 3.5, 0 7" fill="#666" />
        </marker>
      </defs>
      <line
        x1={x1} y1={y1} x2={x2} y2={y2}
        stroke="#666"
        strokeWidth="2"
        strokeDasharray={dashed ? "5,5" : "none"}
        markerEnd="url(#arrowhead)"
      />
      {label && (
        <text x={(x1+x2)/2} y={(y1+y2)/2 - 5} textAnchor="middle" fontSize="10" fill="#666">
          {label}
        </text>
      )}
    </g>
  );

  return (
    <div className="w-full bg-gray-900 rounded-lg p-4">
      <h2 className="text-white text-xl font-bold text-center mb-4">
        DecarbIQ Energy Price Relationship Model
      </h2>
      <p className="text-gray-400 text-center text-sm mb-4">
        Click nodes to highlight • Coefficients show $/MMBtu impact on gas price
      </p>
      
      <svg viewBox="0 0 800 600" className="w-full">
        {/* Background */}
        <rect width="800" height="600" fill="#1a1a2e" />
        
        {/* Upstream Section Label */}
        <text x="200" y="30" fill="#888" fontSize="12" textAnchor="middle">UPSTREAM</text>
        
        {/* Supply Node */}
        <NodeBox id="supply" data={nodeData.supply} x={20} y={50} />
        
        {/* Demand Node */}
        <NodeBox id="demand" data={nodeData.demand} x={240} y={50} />
        
        {/* Policy Node */}
        <NodeBox id="policy" data={nodeData.policy} x={460} y={50} />
        
        {/* Arrows to Gas Price */}
        <Arrow x1={120} y1={180} x2={350} y2={250} />
        <Arrow x1={340} y1={170} x2={380} y2={250} />
        <Arrow x1={560} y1={180} x2={430} y2={250} dashed={true} label="modifies" />
        
        {/* Natural Gas Price - Central Node */}
        <g transform="translate(300, 260)">
          <rect width="200" height="80" rx="10" fill="#e74c3c" stroke="#c0392b" strokeWidth="3" />
          <text x="100" y="30" textAnchor="middle" fill="white" fontWeight="bold" fontSize="16">
            NATURAL GAS PRICE
          </text>
          <text x="100" y="50" textAnchor="middle" fill="white" fontSize="12">
            Henry Hub $/MMBtu
          </text>
          <text x="100" y="70" textAnchor="middle" fill="white" fontSize="11">
            R² = 0.76 | MAE = $0.72
          </text>
        </g>
        
        {/* Arrow to Generation Mix */}
        <Arrow x1={400} y1={340} x2={400} y2={370} label="passthrough" />
        
        {/* Generation Mix - Moderator */}
        <g transform="translate(300, 380)">
          <rect width="200" height="90" rx="8" fill="#f1c40f" />
          <text x="100" y="22" textAnchor="middle" fill="#333" fontWeight="bold" fontSize="14">
            GENERATION MIX
          </text>
          <text x="100" y="42" textAnchor="middle" fill="#333" fontSize="10">(Moderator)</text>
          <text x="20" y="62" fill="#333" fontSize="11">Gas: 43% (r=+0.81)</text>
          <text x="20" y="80" fill="#333" fontSize="11">Coal: 16% (r=-0.72)</text>
        </g>
        
        {/* Arrow to Electricity */}
        <Arrow x1={400} y1={470} x2={400} y2={500} />
        
        {/* Electricity Price - Downstream */}
        <g transform="translate(150, 510)">
          <rect width="500" height="80" rx="10" fill="#e67e22" />
          <text x="250" y="22" textAnchor="middle" fill="white" fontWeight="bold" fontSize="14">
            ELECTRICITY PRICE
          </text>
          
          {/* TX Box */}
          <rect x="20" y="35" width="220" height="40" rx="5" fill="#d35400" />
          <text x="130" y="52" textAnchor="middle" fill="white" fontWeight="bold" fontSize="12">TEXAS</text>
          <text x="130" y="68" textAnchor="middle" fill="white" fontSize="10">
            r=+0.68 | 6.2¢/kWh | Gas-driven
          </text>
          
          {/* CA Box */}
          <rect x="260" y="35" width="220" height="40" rx="5" fill="#d35400" />
          <text x="370" y="52" textAnchor="middle" fill="white" fontWeight="bold" fontSize="12">CALIFORNIA</text>
          <text x="370" y="68" textAnchor="middle" fill="white" fontSize="10">
            r=-0.35 | 12.0¢/kWh | Policy-driven
          </text>
        </g>
        
        {/* Legend */}
        <g transform="translate(620, 400)">
          <rect width="160" height="120" rx="5" fill="#2a2a4a" />
          <text x="80" y="20" textAnchor="middle" fill="white" fontWeight="bold" fontSize="12">KEY INSIGHTS</text>
          <text x="10" y="40" fill="#aaa" fontSize="9">• TX follows gas (+0.68)</text>
          <text x="10" y="55" fill="#aaa" fontSize="9">• CA inverts gas (-0.35)</text>
          <text x="10" y="70" fill="#aaa" fontSize="9">• Shale ≈ RGGI impact</text>
          <text x="10" y="85" fill="#aaa" fontSize="9">• Coal ret. ↑ elec prices</text>
          <text x="10" y="100" fill="#aaa" fontSize="9">• Regional passthrough</text>
          <text x="10" y="115" fill="#aaa" fontSize="9">  varies 0.2 - 0.8</text>
        </g>
      </svg>
      
      {selectedNode && (
        <div className="mt-4 p-4 bg-gray-800 rounded-lg">
          <h3 className="text-white font-bold">{nodeData[selectedNode]?.title} Details</h3>
          <div className="text-gray-300 text-sm mt-2">
            {nodeData[selectedNode]?.items?.map((item, i) => (
              <div key={i} className="flex justify-between py-1 border-b border-gray-700">
                <span>{item.name}</span>
                <span className={item.coef > 0 ? "text-green-400" : "text-red-400"}>
                  {item.coef !== undefined ? (item.coef > 0 ? '+' : '') + item.coef.toFixed(2) : item.value}
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
