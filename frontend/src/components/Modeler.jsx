import React from 'react'
import { api } from '../api'

export function Modeler({ meta, reload }) {
  const [tab, setTab] = React.useState('calc')
  const [msg, setMsg] = React.useState('')

  return (
    <div className="modeler">
      <div className="card">
        <h3>数据模型</h3>
        <div className="tabs">
          <button className={tab === 'tables' ? 'active' : ''} onClick={() => setTab('tables')}>表与关联</button>
          <button className={tab === 'dims' ? 'active' : ''} onClick={() => setTab('dims')}>维度/层级</button>
          <button className={tab === 'measures' ? 'active' : ''} onClick={() => setTab('measures')}>度量</button>
          <button className={tab === 'calc' ? 'active' : ''} onClick={() => setTab('calc')}>计算字段</button>
        </div>
        <p className="muted" style={{ marginTop: 8 }}>
          当前版本随服务自带样例模型；计算字段支持在界面里即时校验表达式（保存即检查错误位置）。
        </p>
        <button
          className="btn"
          onClick={async () => {
            await api.resetSample()
            setMsg('已重置为样例模型')
            reload()
          }}
        >
          重置为样例模型
        </button>{' '}
        {msg && <span className="expr-ok">{msg}</span>}
      </div>

      {tab === 'tables' && <TablesJoins meta={meta} />}
      {tab === 'dims' && <Dimensions meta={meta} />}
      {tab === 'measures' && <Measures meta={meta} />}
      {tab === 'calc' && <CalcFields meta={meta} />}
    </div>
  )
}

function TablesJoins({ meta }) {
  return (
    <>
      <div className="card">
        <h3>物理表（{meta.tables.length}）</h3>
        {meta.tables.map((t) => (
          <div key={t.id} className="list-row">
            <span><strong>{t.name}</strong> <span className="tag">{t.physical_name}</span></span>
            <span className="muted">
              {t.columns.map((c) => c.name + (c.primary_key ? ' 🔑' : '')).join(', ')}
            </span>
          </div>
        ))}
      </div>
      <div className="card">
        <h3>表关联（{meta.joins.length}）</h3>
        {meta.joins.map((j) => (
          <div key={j.id} className="list-row">
            <span>
              <strong>{j.left_table}</strong> → <strong>{j.right_table}</strong>{' '}
              <span className="tag">
                {j.cardinality === 'one_to_one' ? '一对一' :
                 j.cardinality === 'one_to_many' ? '一对多' : '多对多'}
              </span>
            </span>
            <span className="muted">
              {j.keys.map((k) => `${j.left_table}.${k.left_column} = ${j.right_table}.${k.right_column}`).join(' AND ')}
            </span>
          </div>
        ))}
      </div>
    </>
  )
}

function Dimensions({ meta }) {
  return (
    <div className="card">
      <h3>维度与层级钻取路径</h3>
      {Object.values(meta.hierarchies).map((h) => (
        <div key={h.id} className="list-row">
          <span><strong>{h.name}</strong></span>
          <span>{h.levels.map((id) => meta.dimensions[id]?.name).join(' → ')}</span>
        </div>
      ))}
      <h3 style={{ marginTop: 14 }}>全部维度</h3>
      {Object.values(meta.dimensions).map((d) => (
        <div key={d.id} className="list-row">
          <span>{d.name} <span className="tag">{d.table}</span></span>
          <span className="muted">{d.data_type}{d.hierarchy_id ? ` · ${d.hierarchy_id}` : ''}</span>
        </div>
      ))}
    </div>
  )
}

function Measures({ meta }) {
  return (
    <div className="card">
      <h3>度量</h3>
      {Object.values(meta.measures).map((m) => (
        <div key={m.id} className="list-row">
          <span>{m.name} <span className="tag">{m.table}</span></span>
          <span className="muted">{m.agg}</span>
        </div>
      ))}
    </div>
  )
}

function CalcFields({ meta }) {
  const [table, setTable] = React.useState(meta.tables[0]?.id)
  const [expr, setExpr] = React.useState('')
  const [check, setCheck] = React.useState(null)
  const [highlight, setHighlight] = React.useState(null)

  const validate = async () => {
    try {
      const r = await api.validateExpression({ table, expression: expr })
      setCheck(r)
      setHighlight(null)
    } catch (e) {
      setCheck({ ok: false, error: { message: e.message } })
    }
  }

  return (
    <div className="card">
      <h3>计算字段表达式沙箱（保存时即校验，错误位置会标出）</h3>
      <div className="grid-form">
        <label>
          所在表
          <select value={table} onChange={(e) => setTable(e.target.value)}>
            {meta.tables.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
      </div>
      <div style={{ marginTop: 8 }}>
        <textarea
          style={{ width: '100%', minHeight: 90, fontFamily: 'monospace' }}
          placeholder="例如：IF(unit_price >= 500, &quot;高价&quot;, IF(unit_price >= 150, &quot;中价&quot;, &quot;低价&quot;))"
          value={expr}
          onChange={(e) => {
            setExpr(e.target.value)
            setHighlight(null)
          }}
        />
      </div>
      <button className="btn primary" style={{ marginTop: 6 }} onClick={validate}>
        校验表达式
      </button>
      {check?.ok && <div className="expr-ok">✓ 语法与类型校验通过，推断类型：{check.data_type}</div>}
      {check && !check.ok && (
        <div className="expr-error">
          ✗ {check.error.message}
          {typeof check.error.start === 'number' && (
            <div style={{ marginTop: 4, color: '#1f2430' }}>
              <pre style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{markExpr(expr, check.error)}</pre>
            </div>
          )}
        </div>
      )}

      <h3 style={{ marginTop: 18 }}>已定义的计算字段</h3>
      {Object.values(meta.calculated_fields).map((c) => (
        <div key={c.id} className="list-row">
          <span>{c.name} <span className="tag">{c.table}</span></span>
          <span className="muted">{c.data_type}</span>
        </div>
      ))}
      {highlight}
    </div>
  )
}

function markExpr(src, err) {
  const s = err.start ?? 0
  const e = Math.max(s + 1, err.end ?? s + 1)
  return `${src.slice(0, s)}【${src.slice(s, e)}】${src.slice(e)}\n${' '.repeat(s)}^^ 错误位置`
}
