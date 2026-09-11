# Vercel Deploy заавар

## Бүтэц
- **Frontend** (Next.js) → Vercel дээр
- **Backend** (FastAPI + GraphRAG) → тусдаа сервер (Railway / Render / VPS)

> GraphRAG subprocess ажиллуулдаг тул backend-ийг Vercel дээр байрлуулах боломжгүй.

---

## 1. Backend-ийг Railway дээр deploy хийх

1. https://railway.app бүртгэл үүсгэнэ
2. New Project → Deploy from GitHub repo
3. `main_api.py` болон `requirements.txt` байгаа repo-г холбоно
4. Environment Variables нэмнэ:
   ```
   GRAPHRAG_API_KEY=your_key_here
   GRAPHRAG_ROOT=.
   ```
5. Deploy хийсний дараа URL авна: `https://your-app.railway.app`

**requirements.txt:**
```
fastapi
uvicorn
python-dotenv
graphrag
```

---

## 2. Frontend-ийг Vercel дээр deploy хийх

1. `central-test-nextjs` хавтасыг GitHub repo болгоно
2. https://vercel.com → New Project → GitHub repo-г холбоно
3. Environment Variables нэмнэ:
   ```
   NEXT_PUBLIC_API_URL=https://your-app.railway.app
   NEXT_PUBLIC_API_KEY=your_key_here
   ```
4. Deploy дарна — автоматаар build хийнэ

---

## 3. Локал ажиллуулах

```bash
cd central-test-nextjs
cp .env.local.example .env.local
# .env.local файлд API URL болон KEY-г тохируулна
npm install
npm run dev
# http://localhost:3000
```
