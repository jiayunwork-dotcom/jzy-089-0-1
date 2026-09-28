import React from 'react'
import { createRoot } from 'react-dom/client'
import { api } from './api'
import { QueryBuilder } from './components/QueryBuilder'
import { Modeler } from './components/Modeler'
import './styles.css'

function App() {
  const [tab, setTab] = React.useState('query')
  const [meta, setMeta] = React.useState({
    tables: [], joins: [], dimensions: {}, hierarchies: {},
    measures: {}, calculated_fields: {},
  })
  const [loadError, setLoadError] = React.useState('')

  const loadMeta = React.useCallback(async () => {
    try {
      const data = await api.meta()
      setMeta({
        tables: data.tables,
        joins: data.joins,
        dimensions: index(data.dimensions),
        hierarchies: index(data.hierarchies),
        measures: index(data.measures),
        calculated_fields: index(data.calculated_fields),
      })
    } catch (e) {
      setLoadError(e.message)
    }
  }, [])

  React.useEffect(() => { loadMeta() }, [loadMeta])

  return (
    <div className="app">
      <div className="topbar">
        <h1>拖拽式自助数据查询</h1>
        <div className="tabs">
          <button className={tab === 'query' ? 'active' : ''} onClick={() => setTab('query')}>
            查询构建
          </button>
          <button className={tab === 'model' ? 'active' : ''} onClick={() => { setTab('model'); loadMeta() }}>
            数据建模
          </button>
        </div>
        <div className="spacer" />
        <span className="muted">拖拽字段 → 自动生成参数化 SQL → 只读执行</span>
      </div>
      {loadError && <div className="error" style={{ margin: 10 }}>模型加载失败：{loadError}</div>}
      {tab === 'query'
        ? <QueryBuilder key="builder" meta={meta} />
        : <Modeler meta={meta} reload={loadMeta} />}
    </div>
  )
}

function index(arr) {
  return Object.fromEntries((arr || []).map((x) => [x.id, x]))
}

createRoot(document.getElementById('root')).render(<App />)
