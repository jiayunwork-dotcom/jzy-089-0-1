import React from 'react'

const TYPE_LABEL = {
  sum: '求和',
  count: '计数',
  count_distinct: '去重计数',
  avg: '平均',
  min: '最小',
  max: '最大',
}

export function FieldPalette({ meta, onDragStart }) {
  const dims = Object.values(meta.dimensions || {})
  const measures = Object.values(meta.measures || {})
  const calcs = Object.values(meta.calculated_fields || {})

  return (
    <div className="sidebar">
      <h3>维度（拖到 行/列/筛选）</h3>
      {dims.map((d) => (
        <div
          key={d.id}
          className="field-chip dim"
          draggable
          onDragStart={(e) =>
            onDragStart(e, { kind: 'dimension', id: d.id, label: d.name })
          }
        >
          <span className="name">{d.name}</span>
          <span className="meta">
            {d.table}
            {d.hierarchy_id ? ` · ${meta.hierarchies[d.hierarchy_id]?.name || ''}` : ''}
          </span>
        </div>
      ))}

      <h3>度量（拖到 数值/筛选）</h3>
      {measures.map((m) => (
        <div
          key={m.id}
          className="field-chip measure"
          draggable
          onDragStart={(e) =>
            onDragStart(e, { kind: 'measure', id: m.id, label: m.name })
          }
        >
          <span className="name">{m.name}</span>
          <span className="meta">{TYPE_LABEL[m.agg] || m.agg} · {m.table}</span>
        </div>
      ))}

      <h3>计算字段（维度可分组；数值默认求和）</h3>
      {calcs.map((c) => (
        <div
          key={c.id}
          className="field-chip calc"
          draggable
          onDragStart={(e) =>
            onDragStart(e, { kind: 'calculated', id: c.id, label: c.name })
          }
        >
          <span className="name">{c.name}</span>
          <span className="meta">{c.table} · {c.data_type}</span>
        </div>
      ))}
    </div>
  )
}
