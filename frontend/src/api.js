const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const text = await res.text()
  const data = text ? JSON.parse(text) : null
  if (!res.ok) {
    const detail = data?.detail
    throw new Error(
      typeof detail === 'string'
        ? detail
        : detail?.message || JSON.stringify(detail),
    )
  }
  return data
}

export const api = {
  meta: () => request('/meta/fields'),
  model: () => request('/model'),
  enumValues: (fieldId) =>
    request(`/meta/enum?field_id=${encodeURIComponent(fieldId)}`),
  translate: (spec) =>
    request('/translate', { method: 'POST', body: JSON.stringify(spec) }),
  query: (spec) =>
    request('/query', { method: 'POST', body: JSON.stringify(spec) }),
  drill: (dimensionId, spec) =>
    request('/drill', {
      method: 'POST',
      body: JSON.stringify({ dimension_id: dimensionId, spec }),
    }),
  validateExpression: (payload) =>
    request('/validate-expression', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  resetSample: () =>
    request('/model/reset-sample', { method: 'POST' }),
}

export function buildSpec(state) {
  return {
    rows: state.rows,
    columns: state.columns,
    values: state.values.map((v) => ({
      measure_id: v.measureId || null,
      field_id: v.fieldId || null,
      agg: v.agg || 'sum',
    })),
    filters: state.filters.map((f) => ({
      field_id: f.fieldId,
      op: f.op,
      value: f.value,
    })),
    sorts: state.sorts,
    limit: state.limit ?? null,
  }
}
