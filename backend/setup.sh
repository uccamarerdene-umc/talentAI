#!/bin/bash
# Central Test AI - Backend автомат суулгагч
set -e

echo "🔧 Virtual environment үүсгэж байна..."
python3 -m venv venv
source venv/bin/activate

echo "📦 Сан суулгаж байна..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "⚙️  .env файл үүсгэж байна..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "✅ .env үүслээ — GRAPHRAG_API_KEY утгыг тохируулна уу"
else
  echo "ℹ️  .env аль хэдийн байна"
fi

echo ""
echo "✅ Backend бэлэн болов!"
echo "   Дараах алхмуудыг гүйцэтгэнэ:"
echo "   1. .env файлд GRAPHRAG_API_KEY тохируулна"
echo "   2. GraphRAG индекс байгуулна:  python3 -m graphrag.index --root ."
echo "   3. Сервер ажиллуулна:          uvicorn main_api:app --reload --port 8000"
