import React from 'react'

export function DropZone({
  title,
  accept,
  items,
  onDrop,
  onRemove,
  highlight,
  renderLabel,
}) {
  const [over, setOver] = React.useState(false)
  return (
    <div
      className={`zone ${over ? 'over' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault()
        setOver(false)
        const payload = JSON.parse(e.dataTransfer.getData('application/json'))
        if (accept.includes(payload.kind)) onDrop(payload)
      }}
    >
      <h4>
        {title}
        {highlight && <span className="tag" style={{ marginLeft: 6 }}>{highlight}</span>}
      </h4>
      {items.length === 0 && <span className="muted">把字段拖到这里</span>}
      {items.map((it, i) => (
        <span key={`${it.id}-${i}`} className="placed" title={it.id}>
          {renderLabel ? renderLabel(it) : it.label}
          {it.drillable && <span title="可下钻">▾</span>}
          <span className="x" onClick={() => onRemove(i)}>
            ×
          </span>
        </span>
      ))}
    </div>
  )
}
