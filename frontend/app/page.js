'use client'

import { useState, useRef, useEffect } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'ac58e02fe0286de3fe21d44967b514ab9e91cfcd31595fd2e767622884caddfa';

const STORAGE_KEY = 'ct_chat_history'

const TEST_LINKS = {
  'CTPI': 'https://talenthub.mn/login',
  'Big5': 'https://talenthub.mn/login',
  'VOC': 'https://talenthub.mn/login',
  'PP Test': 'https://talenthub.mn/login',
  'EQ': 'https://talenthub.mn/login',
  'MOTIVATION+': 'https://talenthub.mn/login',
  'Sales Competency': 'https://talenthub.mn/login',
}

function addTestLinks(text) {
  if (!text) return text
  const url = 'https://talenthub.mn/login'
  const tests = ['Sales Competency', 'MOTIVATION+', 'PP тест', 'PP Test', 'Big5', 'CTPI', 'VOC', 'EQ']
  tests.forEach(test => {
    const e = test.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    // Bold болон plain text аль алинд нь link нэм
    text = text.replace(
      new RegExp('(?<!\\[)\\*\\*(' + e + '[^*]*)\\*\\*(?!\\])', 'g'),
      '**[$1](' + url + ')**'
    )
    // Plain text дотор
    text = text.replace(
      new RegExp('(?<!\\[|\\*)\\b(' + e + ')\\b(?![\\]*\\(])', 'g'),
      '[$1](' + url + ')'
    )
  })
  return text
}

function cleanAnswer(text) {
  if (!text) return ''
  text = addTestLinks(text)
  return text
    .replace(/^SUCCESS:.*\n?/gim, '')
    .replace(/^INFO:.*\n?/gim, '')
    .replace(/^WARNING:.*\n?/gim, '')
    .replace(/^ERROR:.*\n?/gim, '')
    .replace(/^Answer:\s*/im, '')
    .replace(/\[Data:[^\]]*\]/g, '')
    .trim()
}

export default function Home() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [uploadLoading, setUploadLoading] = useState(false)
  const [sessionId] = useState(() => Math.random().toString(36).substr(2, 9))
  const [hasExcel, setHasExcel] = useState(false)
  const fileInputRef = useRef(null)
  const [history, setHistory] = useState([])
  const [showHero, setShowHero] = useState(true)
  const [backendStatus, setBackendStatus] = useState('checking') // 'checking' | 'online' | 'offline'
  const bottomRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]')
      setHistory(saved)
    } catch {}
  }, [])

  useEffect(() => {
    let cancelled = false
    const checkHealth = async () => {
      try {
        const res = await fetch(`${API_URL}/health`)
        if (!cancelled) setBackendStatus(res.ok ? 'online' : 'offline')
      } catch {
        if (!cancelled) setBackendStatus('offline')
      }
    }
    checkHealth()
    const interval = setInterval(checkHealth, 30000)
    return () => { cancelled = true; clearInterval(interval) }
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, loading])

  const uploadExcel = async (file, question) => {
    if (!file || uploadLoading) return
    setShowHero(false)
    setUploadLoading(true)
    const fileName = file.name
    setMessages(prev => [...prev, { role: 'user', text: `📊 ${fileName} файл оруулав — ${question || 'дүн шинжилгээ хийнэ үү'}` }])
    try {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('question', question || 'Энэ өгөгдлийг дүн шинжилгээ хийж дүгнэлт гарга')
      const res = await fetch(`${API_URL}/analyze-excel`, {
        method: 'POST',
        headers: { 'X-API-Key': API_KEY, 'X-Session-Id': sessionId },
        body: formData,
      })
      const data = await res.json()
      if (data.answer) {
        setHasExcel(true)
        setMessages(prev => [...prev, { role: 'ai', text: cleanAnswer(data.answer) }])
      } else {
        setMessages(prev => [...prev, { role: 'ai', text: 'Файл боловсруулахад алдаа гарлаа.' }])
      }
    } catch (e) {
      setMessages(prev => [...prev, { role: 'ai', text: 'Серверт холбогдоход алдаа гарлаа.' }])
    }
    setUploadLoading(false)
  }

  const handleFileChange = (e) => {
    const file = e.target.files[0]
    if (!file) return
    const q = 'Дүн шинжилгээ хийж дүгнэлт гарга'
    uploadExcel(file, q)
    e.target.value = ''
  }

  const sendMessage = async () => {
    const text = input.trim()
    if (!text || loading) return

    setShowHero(false)
    setMessages(prev => [...prev, { role: 'user', text }])
    setInput('')
    setLoading(true)

    try {
      const res = await fetch(`${API_URL}/ask`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-Key': API_KEY,
          'X-Session-Id': sessionId,
        },
        body: JSON.stringify({ prompt: text, method: 'local' }),
      })

      const data = await res.json()

      if (!res.ok || data.error) {
        setMessages(prev => [...prev, { role: 'error', text: data.error || 'Алдаа гарлаа.' }])
      } else {
        const answer = cleanAnswer(data.answer || 'Мэдээлэл олдсонгүй.')
        setMessages(prev => [...prev, { role: 'ai', text: answer }])
        saveHistory(text, answer)
      }
    } catch {
      setMessages(prev => [...prev, { role: 'error', text: '❌ Сервертэй холбогдож чадсангүй.' }])
    } finally {
      setLoading(false)
      inputRef.current?.focus()
    }
  }

  const saveHistory = (q, a) => {
    const item = { id: Date.now(), question: q, answer: a }
    const updated = [item, ...history].slice(0, 15)
    setHistory(updated)
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(updated)) } catch {}
  }

  const loadChat = (item) => {
    setShowHero(false)
    setMessages([
      { role: 'user', text: item.question },
      { role: 'ai', text: item.answer },
    ])
  }

  const newChat = () => {
    setMessages([])
    setShowHero(true)
  }

  return (
    <div className="layout">
      {/* Sidebar */}
      <aside className="sidebar">
        <div className="logo">
          <i className="fa-solid fa-wand-magic-sparkles" />
           TALENT AI
        </div>
        <div
          title={API_URL}
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            fontSize: 12, color: '#94a3b8', padding: '0 4px 12px',
          }}
        >
          <span style={{
            width: 8, height: 8, borderRadius: '50%', flexShrink: 0,
            background: backendStatus === 'online' ? '#22c55e' : backendStatus === 'offline' ? '#ef4444' : '#f59e0b',
          }} />
          {backendStatus === 'online' && 'Backend холбогдсон'}
          {backendStatus === 'offline' && 'Backend холбогдоогүй'}
          {backendStatus === 'checking' && 'Шалгаж байна...'}
        </div>
        <button className="new-chat-btn" onClick={newChat}>
          <i className="fa-solid fa-plus" />
          Шинэ чат эхлэх
        </button>
        <div className="history-label">ЧАТНЫ ТҮҮХ</div>
        <div className="history-list">
          {(() => {
            const now = new Date()
            const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
            const yesterday = new Date(today - 86400000)
            const week = new Date(today - 6 * 86400000)
            const month30 = new Date(today - 29 * 86400000)

            const todayItems = history.filter(i => new Date(i.id) >= today)
            const yesterdayItems = history.filter(i => new Date(i.id) >= yesterday && new Date(i.id) < today)
            const weekItems = history.filter(i => new Date(i.id) >= week && new Date(i.id) < yesterday)
            const month30Items = history.filter(i => new Date(i.id) >= month30 && new Date(i.id) < week)
            const olderItems = history.filter(i => new Date(i.id) < month30)

            const groups = []
            if (todayItems.length) groups.push({ label: 'Өнөөдөр', items: todayItems })
            if (yesterdayItems.length) groups.push({ label: 'Өчигдөр', items: yesterdayItems })
            if (weekItems.length) groups.push({ label: 'Өмнөх 7 хоног', items: weekItems })
            if (month30Items.length) groups.push({ label: 'Өмнөх 30 хоног', items: month30Items })

            const monthGroups = {}
            olderItems.forEach(item => {
              const d = new Date(item.id)
              const key = d.getFullYear() + '-' + d.getMonth()
              const label = d.toLocaleString('mn-MN', { month: 'long', year: 'numeric' })
              if (!monthGroups[key]) monthGroups[key] = { label, items: [] }
              monthGroups[key].items.push(item)
            })
            Object.values(monthGroups).forEach(g => groups.push(g))

            return groups.map((group, idx) => (
              <div key={idx}>
                <div className="history-section-label">{group.label}</div>
                {group.items.map(item => (
                  <div
                    key={item.id}
                    className="history-item"
                    onClick={() => loadChat(item)}
                    title={item.question}
                  >
                    {item.question.substring(0, 35)}...
                  </div>
                ))}
              </div>
            ))
          })()}
        </div>
      </aside>

      {/* Main chat */}
      <main className="main">
        <div className="chat-area">
          {showHero && (
            <div className="hero">
              <div className="orb" />
              <h1>TALENT AI <span>Зөвлөх</span></h1>
              <p>Хиймэл оюун ухаант зөвлөх TALENT AI</p>
            </div>
          )}

          <div className="messages">
            {messages.map((msg, i) => (
              <div key={i} className={`msg ${msg.role}`}>
                {msg.role === 'ai' ? (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({href, children}) => (
                        <a href={href} target="_blank" rel="noopener noreferrer" style={{color:'var(--primary)', fontWeight:700, textDecoration:'underline'}}>
                          {children}
                        </a>
                      ),
                      strong: ({children}) => {
                        const tests = ['CTPI','Big5','VOC','PP Test','PP тест','EQ','MOTIVATION+','Sales Competency']
                        const str = Array.isArray(children) ? children.join('') : String(children || '')
                        const matched = tests.some(t => str.includes(t))
                        if (matched) {
                          return <a href="https://talenthub.mn/login" target="_blank" rel="noopener noreferrer" style={{color:'var(--primary)', fontWeight:700, textDecoration:'underline', cursor:'pointer'}}><strong>{children}</strong></a>
                        }
                        return <strong style={{color:'var(--primary)'}}>{children}</strong>
                      }
                    }}
                  >
                    {msg.text}
                  </ReactMarkdown>
                ) : (
                  msg.text
                )}
              </div>
            ))}

            {loading && (
              <div className="msg ai">
                <div className="typing">
                  <span /><span /><span />
                </div>
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        {/* Input */}
        <div className="input-area">
          <div className="input-box">
            {loading && (
              <div className="loader-text">
                <i className="fa-solid fa-circle-notch fa-spin" /> Шинжилж байна...
              </div>
            )}
            <div className="input-row">
              <i className="fa-solid fa-plus" style={{ color: '#ccc' }} />
              <input
                ref={fileInputRef}
                type="file"
                accept=".xlsx,.xls,.csv"
                style={{display:'none'}}
                onChange={handleFileChange}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={loading || uploadLoading}
                style={{
                  background:'none',
                  border:'1px solid #e2e8f0',
                  borderRadius:'8px',
                  padding:'8px 12px',
                  cursor:'pointer',
                  fontSize:'18px',
                  color:'#64748b',
                  flexShrink:0,
                }}
                title="Excel/CSV файл оруулах"
              >
                📊
              </button>
            <input
                ref={inputRef}
                type="text"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && sendMessage()}
                placeholder="Асуултаа энд бичнэ үү..."
                disabled={loading}
              />
              <button
                className="send-btn"
                onClick={sendMessage}
                disabled={loading || !input.trim()}
              >
                <i className="fa-solid fa-arrow-up" />
              </button>
            </div>
          </div>
        </div>
      </main>

      {/* Instructions */}
      <aside className="instructions">
        <div className="instr-label">АШИГЛАХ ЗААВАР</div>
        <div className="card-3d">
          <i className="fa-solid fa-lightbulb" style={{ color: 'var(--primary)', fontSize: 20 }} />
          <h4>Асуулт асуух</h4>
          <p>Central test-ийн талаар тодорхой асуулт бичиж Enter дарна уу.</p>
        </div>
        <div className="card-3d">
          <i className="fa-solid fa-bolt" style={{ color: 'var(--primary)', fontSize: 20 }} />
          <h4>Өгөгдөлд суурилсан хариулт</h4>
          <p>Ур чадвар болон central тестийн талаарх тодорхой асуултаа бичиж, секундын дотор дэлгэрэнгүй хариулт аваарай.</p>
        </div>
        <div className="card-3d">
          <i className="fa-solid fa-shield-halved" style={{ color: 'var(--primary)', fontSize: 20 }} />
          <h4>Оновчтой тест сонгох</h4>
          <p>Бүх хариулт Central Test мэдээллийн санд тулгуурласан.</p>
        </div>
        <div className="card-3d">
          <i className="fa-solid fa-phone" style={{ color: 'var(--primary)', fontSize: 20 }} />
          <h4>Холбоо барих</h4>
          <p>📞 8804-7823</p>
          <p>🔵 <a href="https://www.facebook.com/unitedconsultingmanagement" target="_blank" style={{color:"var(--primary)"}}>United Consulting Management</a></p>
        </div>
      </aside>
    </div>
  )
}
