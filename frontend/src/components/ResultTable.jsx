import React from 'react'

export function ResultTable({ result, sorts, onSort, onDrill, drillableIds }) {
  if (!result) return null
  const { columns, rows } = result
  const sortByIndex = (i) => onSort(i + 1)

  return (
    <div className="result-card">
      <div className="result-toolbar">
        <strong>结果</strong>
        <span className="muted">{rows.length} 行</span>
        {drillableIds?.size > 0 && (
          <span className="muted">带下划线的维度值可点击向下钻取</span>
        )}
      </div>
      <div style={{ overflow: 'auto', maxHeight: '60vh' }}>
        <table className="result">
          <thead>
            <tr>
              {columns.map((c, i) => {
                const s = sorts.find((x) => x.index === i + 1)
                return (
                  <th
                    key={c.key}
                    className={c.kind === 'value' ? 'num' : ''}
                    onClick={() => sortByIndex(i)}
                  >
                    {c.label}
                    {s && <span className="arrow">{s.dir === 'asc' ? '▲' : '▼'}</span>}
                  </th>
                )
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri}>
                {columns.map((c) => {
                  const drillable =
                    c.kind === 'group' && drillableIds?.has(c.field_id)
                  return (
                    <td
                      key={c.key}
                      className={c.kind === 'value' ? 'num' : ''}
                      style={drillable ? { color: '#2b5cd7', cursor: 'pointer' } : undefined}
                      onClick={() => drillable && onDrill(c.field_id, row[c.key])}
                    >
                      {formatCell(row[c.key])}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function formatCell(v) {
  if (v === null || v === undefined) return '∅'
  if (typeof v === 'number') {
    return Number.isInteger(v) ? v.toLocaleString() : v.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })
  }
  return String(v)
}
