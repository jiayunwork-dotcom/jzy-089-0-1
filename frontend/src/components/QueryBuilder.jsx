import React from 'react'
import { api, buildSpec } from '../api'
import { FieldPalette } from './FieldPalette'
import { DropZone } from './DropZone'
import { FilterEditor } from './FilterEditor'
import { ResultTable } from './ResultTable'

const AGG_OPTIONS = [
  ['sum', '求和'], ['count', '计数'], ['count_distinct', '去重计数'],
  ['avg', '平均'], ['min', '最小'], ['max', '最大'],
]

export function QueryBuilder({ meta }) {
  const [rows, setRows] = React.useState([])
  const [columns, setColumns] = React.useState([])
  const [values, setValues] = React.useState([])
  const [filters, setFilters] = React.useState([])
  const [sorts, setSorts] = React.useState([])
  const [result, setResult] = React.useState(null)
  const [sql, setSql] = React.useState('')
  const [error, setError] = React.useState('')
  const [loading, setLoading] = React.useState(false)
  const limitRef = React.useRef(1000)

  const state = { rows, columns, values, filters, sorts, limit: 1000 }

  const runQuery = React.useCallback(async () => {
    const spec = buildSpec({ ...state, limit: limitRef.current })
    if (spec.rows.length + spec.columns.length + spec.values.length === 0) {
      setResult(null)
      setSql('')
      return
    }
    setLoading(true)
    setError('')
    try {
      const [translated, queried] = await Promise.all([
        api.translate(spec),
        api.query(spec),
      ])
      setSql(translated.sql)
      setResult(queried)
    } catch (e) {
      setError(e.message)
      setResult(null)
    } finally {
      setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, columns, values, filters, sorts])

  React.useEffect(() => {
    const t = setTimeout(runQuery, 300)
    return () => clearTimeout(t)
  }, [runQuery])

  const onDragStart = (e, payload) => {
    e.dataTransfer.setData('application/json', JSON.stringify(payload))
    e.dataTransfer.effectAllowed = 'copy'
  }

  const addGroup = (zone, setZone) => (payload) => {
    if (payload.kind === 'measure') return
    setZone((z) => (z.includes(payload.id) ? z : [...z, payload.id]))
  }
  const addValue = (payload) => {
    if (payload.kind === 'dimension') return
    setValues((vs) => {
      if (vs.some((v) => (v.measureId || v.fieldId) === payload.id)) return vs
      return payload.kind === 'measure'
        ? [...vs, { measureId: payload.id, label: payload.label }]
        : [...vs, { fieldId: payload.id, label: payload.label, agg: 'sum' }]
    })
  }
  const addFilter = (payload) => {
    setFilters((fs) =>
      fs.some((f) => f.fieldId === payload.id)
        ? fs
        : [...fs, { fieldId: payload.id, op: 'eq', value: null }],
    )
  }

  const onSort = (index) => {
    setSorts((ss) => {
      const existing = ss.find((s) => s.index === index)
      if (!existing) return [...ss.filter((s) => s.index !== index), { index, dir: 'asc' }]
      if (existing.dir === 'asc')
        return ss.map((s) => (s.index === index ? { ...s, dir: 'desc' } : s))
      return ss.filter((s) => s.index !== index)
    })
  }

  const hierarchyMap = React.useMemo(() => {
    const m = new Map()
    for (const h of Object.values(meta.hierarchies || {})) {
      h.levels.forEach((id) => m.set(id, h))
    }
    return m
  }, [meta])
  const drillableIds = React.useMemo(() => {
    const s = new Set()
    for (const id of [...rows, ...columns]) {
      const h = hierarchyMap.get(id)
      if (h && h.levels.indexOf(id) < h.levels.length - 1) s.add(id)
    }
    return s
  }, [rows, columns, hierarchyMap])

  const onDrill = async (dimensionId) => {
    const spec = buildSpec({ ...state, limit: limitRef.current })
    setLoading(true)
    setError('')
    try {
      const queried = await api.drill(dimensionId, spec)
      // 钻取等价于在行上追加更细一级
      const h = hierarchyMap.get(dimensionId)
      const nxt = h.levels[h.levels.indexOf(dimensionId) + 1]
      setRows((r) => (r.includes(nxt) ? r : [...r, nxt]))
      setResult(queried)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const valueItems = values.map((v) => ({
    id: v.measureId || v.fieldId,
    label: v.label,
  }))

  return (
    <div className="builder">
      <FieldPalette meta={meta} onDragStart={onDragStart} />
      <div className="main">
        <div className="zones">
          <DropZone
            title="行（分组维度，自上而下）"
            accept={['dimension', 'calculated']}
            items={rows.map((id) => ({ id, label: labelOf(meta, id), drillable: drillableIds.has(id) }))}
            onDrop={addGroup('rows', setRows)}
            onRemove={(i) => setRows(rows.filter((_, j) => j !== i))}
          />
          <DropZone
            title="列（附加分组；结果仍为扁平表）"
            accept={['dimension', 'calculated']}
            items={columns.map((id) => ({ id, label: labelOf(meta, id), drillable: drillableIds.has(id) }))}
            onDrop={addGroup('columns', setColumns)}
            onRemove={(i) => setColumns(columns.filter((_, j) => j !== i))}
          />
          <div
            className="zone values"
            style={{ borderStyle: 'solid' }}
            onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' }}
            onDrop={(e) => {
              e.preventDefault()
              const p = JSON.parse(e.dataTransfer.getData('application/json'))
              addValue(p)
            }}
          >
            <h4>数值（度量 / 计算字段聚合）</h4>
            {values.length === 0 && <span className="muted">把度量或计算字段拖到这里</span>}
            {values.map((v, i) => (
              <span key={i} className="placed">
                {v.label}
                {v.fieldId && (
                  <select
                    value={v.agg}
                    style={{ border: 'none', background: 'transparent', fontSize: 12 }}
                    onChange={(e) =>
                      setValues(values.map((x, j) => j === i ? { ...x, agg: e.target.value } : x))
                    }
                  >
                    {AGG_OPTIONS.map(([a, l]) => <option key={a} value={a}>{l}</option>)}
                  </select>
                )}
                <span className="x" onClick={() => setValues(values.filter((_, j) => j !== i))}>×</span>
              </span>
            ))}
          </div>
          <FilterEditor filters={filters} setFilters={setFilters} meta={meta} />
          <div
            onDragOver={(e) => {
              e.preventDefault()
              e.dataTransfer.dropEffect = 'copy'
            }}
            onDrop={(e) => {
              e.preventDefault()
              const p = JSON.parse(e.dataTransfer.getData('application/json'))
              addFilter(p)
            }}
            style={{ display: 'none' }}
          />
        </div>

        <div style={{ marginTop: 10, display: 'flex', gap: 8, alignItems: 'center' }}>
          <label className="muted">
            行数上限
            <input
              type="number"
              defaultValue={1000}
              style={{ width: 90, marginLeft: 6 }}
              onChange={(e) => (limitRef.current = Number(e.target.value) || 1000)}
            />
          </label>
          <button className="btn primary" onClick={runQuery} disabled={loading}>
            {loading ? '查询中…' : '运行 / 刷新'}
          </button>
        </div>

        {error && <div className="error">{error}</div>}
        <ResultTable
          result={result}
          sorts={sorts}
          onSort={onSort}
          onDrill={onDrill}
          drillableIds={drillableIds}
        />
        {sql && (
          <details className="sql-box" open={false}>
            <summary>生成的 SQL（参数化，只读 SELECT）</summary>
            {sql}
          </details>
        )}
      </div>
    </div>
  )
}

function labelOf(meta, id) {
  return (
    meta.dimensions?.[id]?.name ||
    meta.measures?.[id]?.name ||
    meta.calculated_fields?.[id]?.name ||
    id
  )
}
