export async function POST(request) {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || process.env.API_URL || 'http://localhost:8000'
  const apiKey = process.env.NEXT_PUBLIC_API_KEY || process.env.API_KEY || ''
  const sessionId = request.headers.get('X-Session-Id') || 'default'
  try {
    const formData = await request.formData()
    const res = await fetch(`${apiUrl}/analyze-excel`, {
      method: 'POST',
      headers: {
        'X-API-Key': apiKey,
        'X-Session-Id': sessionId,
      },
      body: formData,
    })
    const data = await res.json()
    return Response.json(data, { status: res.status })
  } catch (err) {
    return Response.json({ error: 'Backend холбогдсонгүй: ' + err.message }, { status: 500 })
  }
}
