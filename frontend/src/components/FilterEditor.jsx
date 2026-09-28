import React from 'react'
import { api } from '../api'

const TEXT_OPS = [
  ['eq', '等于'], ['ne', '不等于'], ['in', '属于'], ['not_in', '不属于'],
  ['starts_with', '开头是'], ['ends_with', '结尾是'], ['contains', '包含'],
  ['is_null', '为空'], ['not_null', '非空'],
]
const NUM_OPS = [
  ['eq', '等于'], ['ne', '不等于'], ['gt', '大于'], ['gte', '大于等于'],
  ['lt', '小于'], ['lte', '小于等于'], ['between', '介于'],
  ['is_null', '为空'], ['not_null', '非空'],
]
const DATE_OPS = [
  ['eq', '等于'], ['between', '日期区间'], ['gt', '晚于'], ['lt', '早于'],
  ['is_null', '为空'], ['not_null', '非空'],
]

export function fieldKinds(meta) {
  const kinds = {}
  for (const d of Object.values(meta.dimensions || {}))
    kinds[d.id] = { type: d.data_type, label: d.name, hierarchy: d.hierarchy_id }
  for (const m of Object.values(meta.measures || {}))
    kinds[m.id] = { type: 'numeric', label: m.name, measure: true }
  for (const c of Object.values(meta.calculated_fields || {}))
    kinds[c.id] = { type: c.data_type, label: c.name }
  return kinds
}

export function opsFor(type, isMeasure) {
  if (isMeasure) return NUM_OPS
  if (type === 'date' || type === 'timestamp') return DATE_OPS
  if (['integer', 'numeric'].includes(type)) return NUM_OPS
  return TEXT_OPS
}

export function FilterEditor({ filters, setFilters, meta }) {
  const kinds = fieldKinds(meta)
  const [enums, setEnums] = React.useState({})

  React.useEffect(() => {
    filters.forEach((f) => {
      const info = kinds[f.fieldId]
      if (
        info && (info.type === 'text') &&
        !enums[f.fieldId] && ['eq', 'in'].includes(f.op)
      ) {
        api.enumValues(f.fieldId).then((d) =>
          setEnums((s) => ({ ...s, [f.fieldId]: d.values })),
        ).catch(() => {})
      }
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters])

  const allFields = [
    ...Object.values(meta.dimensions || {}).map((d) => [d.id, d.name]),
    ...Object.values(meta.measures || {}).map((m) => [m.id, m.name + '（度量）']),
    ...Object.values(meta.calculated_fields || {}).map((c) => [c.id, c.name]),
  ]

  const update = (i, patch) =>
    setFilters(filters.map((f, j) => (j === i ? { ...f, ...patch } : f)))

  const addFilterFromDrop = (payload) => {
    if (filters.some((f) => f.fieldId === payload.id)) return
    setFilters([...filters, { fieldId: payload.id, op: 'eq', value: null }])
  }

  return (
    <div
      className="zone filters"
      style={{ borderStyle: 'solid' }}
      onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' }}
      onDrop={(e) => {
        e.preventDefault()
        addFilterFromDrop(JSON.parse(e.dataTransfer.getData('application/json')))
      }}
    >
      <h4>筛选</h4>
      {filters.length === 0 && <span className="muted">把字段拖到这里，或点击“添加筛选”</span>}
      {filters.map((f, i) => {
        const info = kinds[f.fieldId] || {}
        const ops = opsFor(info.type, info.measure)
        const noValue = ['is_null', 'not_null'].includes(f.op)
        const values = enums[f.fieldId]
        return (
          <div className="filter-row" key={i}>
            <select value={f.fieldId} onChange={(e) => update(i, { fieldId: e.target.value, op: 'eq', value: null })}>
              {allFields.map(([id, name]) => (
                <option key={id} value={id}>{name}</option>
              ))}
            </select>
            <select value={f.op} onChange={(e) => update(i, { op: e.target.value, value: null })}>
              {ops.map(([op, label]) => (
                <option key={op} value={op}>{label}</option>
              ))}
            </select>
            {!noValue && info.type === 'text' && f.op === 'in' && values && (
              <select
                multiple
                style={{ minWidth: 180, minHeight: 56 }}
                value={Array.isArray(f.value) ? f.value : []}
                onChange={(e) =>
                  update(i, {
                    value: [...e.target.selectedOptions].map((o) => o.value),
                  })
                }
              >
                {values.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            )}
            {!noValue && info.type === 'text' && f.op !== 'in' && (
              <input
                value={f.value ?? ''}
                list={`enum-${f.fieldId}`}
                onChange={(e) => update(i, { value: e.target.value })}
              />
            )}
            {!noValue && info.type === 'text' && f.op === 'in' && !values && (
              <input
                placeholder="多个值用逗号分隔"
                onChange={(e) => update(i, { value: e.target.value.split(',').map((s) => s.trim()) })}
              />
            )}
            {!noValue && (info.type === 'date') && f.op === 'between' && (
              <>
                <input type="date" onChange={(e) => update(i, { value: [e.target.value, f.value?.[1] ?? null] })} />
                <span>至</span>
                <input type="date" onChange={(e) => update(i, { value: [f.value?.[0] ?? null, e.target.value] })} />
              </>
            )}
            {!noValue && info.type === 'date' && f.op !== 'between' && (
              <input type="date" value={f.value ?? ''} onChange={(e) => update(i, { value: e.target.value })} />
            )}
            {!noValue && (info.type === 'integer' || info.type === 'numeric') && f.op === 'between' && (
              <>
                <input type="number" onChange={(e) => update(i, { value: [Number(e.target.value), f.value?.[1] ?? null] })} />
                <span>至</span>
                <input type="number" onChange={(e) => update(i, { value: [f.value?.[0] ?? null, Number(e.target.value)] })} />
              </>
            )}
            {!noValue && (info.type === 'integer' || info.type === 'numeric') && f.op !== 'between' && (
              <input type="number" value={f.value ?? ''} onChange={(e) => update(i, { value: Number(e.target.value) })} />
            )}
            <datalist id={`enum-${f.fieldId}`}>
              {(values || []).map((v) => <option key={v} value={v} />)}
            </datalist>
            <span className="x" style={{ cursor: 'pointer', color: '#c0392b' }} onClick={() => setFilters(filters.filter((_, j) => j !== i))}>
              删除
            </span>
          </div>
        )
      })}
      <div style={{ marginTop: 6 }}>
        <button className="btn" onClick={() =>
          setFilters([...filters, { fieldId: allFields[0][0], op: 'eq', value: null }])
        }>
          + 添加筛选
        </button>
      </div>
    </div>
  )
}
