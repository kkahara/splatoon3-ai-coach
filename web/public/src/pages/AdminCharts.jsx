export function NarrowingFunnel({ rows }) {
  const max = Math.max(1, ...rows.map((row) => row.count));
  return (
    <div className="admin-funnel" role="img" aria-label="Selection funnel">
      {rows.map((row) => (
        <div key={row.label} style={{ width: `${Math.max(12, (row.count / max) * 100)}%` }}>
          <span>{row.label}</span>
          <strong>{row.count}</strong>
        </div>
      ))}
    </div>
  );
}

export function CountBars({ rows, label }) {
  const max = Math.max(1, ...rows.map((row) => row.count));
  const width = 360;
  const bar = width / Math.max(rows.length, 1);
  return (
    <svg className="admin-chart" viewBox={`0 0 ${width} 110`} role="img" aria-label={label}>
      {rows.map((row, index) => {
        const height = (row.count / max) * 78;
        return (
          <g key={row.label}>
            <rect x={index * bar + 6} y={84 - height} width={Math.max(bar - 12, 4)} height={height} />
            <text x={index * bar + bar / 2} y={100} textAnchor="middle">
              {row.count}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export function CountTable({ rows }) {
  return (
    <table>
      <thead>
        <tr>
          <th>Label</th>
          <th>Count</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label}>
            <td>{row.label}</td>
            <td>{row.count}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
