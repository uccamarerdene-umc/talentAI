export async function POST(request) {
  const body = await request.json()
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || ''
  const apiKey = process.env.NEXT_PUBLIC_API_KEY || ''
 
  try {
    const res = await fetch(`${apiUrl}/ask`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-API-Key': apiKey,
      },
      body: JSON.stringify(body),
    })
    const data = await res.json()
    return Response.json(data, { status: res.status })
  } catch (err) {
    return Response.json({ error: 'Backend холбогдсонгүй: ' + err.message }, { status: 500 })
  }
}
 




