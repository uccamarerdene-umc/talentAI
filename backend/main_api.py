"""
main_api.py — Talent AI FastAPI Backend v2.5.0
Fixes v2.5:
  - google.generativeai → google.genai (шинэ SDK, requirements.txt-тай нийцүүлсэн)
  - Gemini multi-turn chat history зөв дамждаг
  - GraphRAG байхгүй/муу хариулсан ч Gemini ЗААВАЛ ажилладаг
  - User асуулт ҮРГЭЛЖ DB-д хадгалагддаг
  - "мэдээлэл хангалтгүй" гэж хариулах боломжгүй

Энэ хувилбар: GIT main_api.py-г суурь болгож, системийн prompt-ыг
CENTRAL TEST-ийн мэдлэгийн сангаас хариулт бичих
нарийвчилсан загвараар баяжуулсан.
"""

import os
import re
import logging
import asyncio
from typing import Optional, List, Dict, Any

from dotenv import load_dotenv
from google import genai
from google.genai import types

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from db import (
    init_db, create_session, get_session, update_session_file,
    get_all_sessions, delete_session, save_message, get_messages,
)
from excel_processor import process_excel

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s — %(message)s")
logger = logging.getLogger("talent_ai")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-3.6-flash"
ERROR_FALLBACK_TEXT = "Системд техникийн алдаа гарлаа. Түр хүлээгээд дахин оролдоно уу."

# google-genai SDK client
_genai_client: Optional[genai.Client] = None

def get_genai_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY environment variable not set")
        _genai_client = genai.Client(api_key=GEMINI_API_KEY)
    return _genai_client


_search_engine = None

def get_local_search_engine():
    global _search_engine
    if _search_engine is None:
        try:
            from graphrag_engine import build_local_search_engine  # type: ignore
            _search_engine = build_local_search_engine()
            logger.info("GraphRAG initialised.")
        except Exception:
            logger.error(
                "GraphRAG index unavailable — every request will fall back to "
                "un-indexed Gemini answers until this is fixed.", exc_info=True,
            )
            raise
    return _search_engine


# ═══════════════════════════════════════════════════════════════════════════
#  STATE
# ═══════════════════════════════════════════════════════════════════════════

class AgentState(BaseModel):
    session_id: str
    user_prompt: str
    chat_history: List[Dict[str, Any]] = []
    has_excel: bool = False
    excel_stats: Optional[str] = Field(default=None)
    detected_test_name: Optional[str] = Field(default=None)
    graphrag_context: Optional[str] = Field(default=None)
    graphrag_used: bool = False
    raw_analysis: Optional[str] = Field(default=None)
    final_output: Optional[str] = Field(default=None)
    had_error: bool = False


# ═══════════════════════════════════════════════════════════════════════════
#  TEXT CLEANUP
# ═══════════════════════════════════════════════════════════════════════════

_TEXT_REPLACEMENTS = {
    "Ниймэл": "Нийтэч", "ниймэл": "нийтэч", "Ниймтэй": "Нийтэч", "ниймтэй": "нийтэч",
    "Нийгэмч": "Нийтэч", "нийгэмч": "нийтэч", "Ниймч": "Нийтэч", "ниймч": "нийтэч",
    "Нийрч": "Нийтэч", "нийрч": "нийтэч", "Ний тэч": "Нийтэч", "ний тэч": "нийтэч",
    "Ниймц": "Нийтэч", "ниймц": "нийтэч", "Н ягт": "Нягт",
    "удирдамжийн": "удирдлагын", "Удирдамжийн": "Удирдлагын",
    "эергээр": "эерэгээр", "үр бүтээлтэй": "бүтээмжтэй", "үр бүтээл": "бүтээмж",
}

def apply_mongolian_regex_fixes(text: str) -> str:
    if not text:
        return text
    for old, new in _TEXT_REPLACEMENTS.items():
        text = text.replace(old, new)
    patterns = [
        (r"^[ \t]*[-\*\+]\s+", "", re.MULTILINE),
        (r"^[ \t]*\d+[\.\)]\s+", "", re.MULTILINE),
        (r"\.{2,}", "…"), (r"\s{2,}", " "), (r" \n", "\n"), (r"\n{3,}", "\n\n"),
        (r"\bгэж\s+байна\b", "гэнэ"), (r"\bбайгаа\s+юм\b", "байна"),
        (r"\|.*?\|", ""), (r"_{1,2}([^_]+)_{1,2}", r"\1"), (r"#+\s", ""),
        (r"```[\s\S]*?\n```", ""), (r"`([^`]+)`", r"\1"), (r"[ \t]+$", ""),
    ]
    for item in patterns:
        text = re.sub(item[0], item[1], text, flags=item[2]) if len(item) == 3 else re.sub(item[0], item[1], text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════════════════
#  СИСТЕМИЙН PROMPT
# ═══════════════════════════════════════════════════════════════════════════

HMM_KNOWLEDGE = """
═══════════════════════════════════════════════════
CENTRAL TEST ТЕСТҮҮДИЙН ИЖ БҮРЭН МЭДЛЭГИЙН САН
═══════════════════════════════════════════════════

1. CTPI — Менежерийн ур чадварын тест (61 ур чадвар, 9 бүлэг)
   Ур чадварын оноо 0-49=хөгжүүлэх шаардлагатай
   Ур чадварын оноо 50-69=хөгжиж буй дунд түвшин (СУЛ ТАЛ БИШ!)
   Ур чадварын оноо 70-100=сайн хөгжсөн буюу баталгаатай түвшин,

  CTPI ТЕСТ — 61 УР ЧАДВАР ↔ HARVARD MANAGEMENTOR МОДУЛИЙН ТОХИРУУЛГА
  Ур чадварын жагсаалтуудыг хөгжүүлэх сургалтын сэдэв дотроос тохирох сургалтуудыг харгалзуулан авна. 

  АНАЛИЗ, ДҮН ШИНЖИЛГЭЭ ХИЙХ ЧАДАМЖ
  Анализ хийх чадвар :доош → HMM: Decision Making, Business acumen
  Шийдвэр гаргах чадвар :	доош → HMM: Decision Making
  Мэдлэг, туршлага хуримтлуулах чадвар:	доош → HMM: Career management
  Мэдлэг туршлагаа хуваалцах чадвар :	доош → HMM: Coaching, Leveraging Your Network
  Аливааг хурдан сурах чадвар :	доош → HMM: Digital Intelligence, Career management
  Бодолтой байх чадвар :	доош → HMM: Strategic Thinking

  БОРЛУУЛАЛТЫН ЧАДАМЖ
  Хэлцэл тохиролцоог амжилттай дуусгах чадвар :	доош → HMM: Negotiating, "NEGOTIATING" case
  Хэрэглэгчийг сэтгэл ханамжтай байлгах чадвар :	доош → HMM: Customer Focus
  Хэрэглэгчийн сэтгэл хөдлөл мэдрэмжийг нэн тэргүүнд тавих чадвар :	доош → HMM: Customer Focus
  Бизнесийн боломжийг тодорхойлох чадвар :	доош → HMM: Business acumen, Business Case Development
  Хүмүүсийг холбох, танилын хүрээгээ тэлэх чадвар :	доош → HMM: Leveraging Your Network
  Үр дүнг төсөөлүүлэн тайлбарлах чадвар :	доош → HMM: Presentation skill, Persuading Others
  Шинэ хэрэглэгч олох чадвар :	доош → HMM: Marketing Essentials
  Борлуулалт хийх чадвар :	доош → HMM: Persuading Others
  Стратеги борлуулалт хийх чадвар :	доош → HMM: Strategic Thinking, Persuading Others
  Хэрэглэгчийн хэрэгцээг ойлгох чадвар :	доош → HMM: Customer Focus

  ХАРИЛЦААНЫ ЧАДАМЖ
  Бусдыг татах чадвар :	доош → HMM: Persuading Others
  Тохиролцол, хэлцэл хийх чадвар :	доош → HMM: Negotiating, "NEGOTIATING" case
  Бусдад нөлөөлөх чадвар :	доош → HMM: Persuading Others
  Сонсох чадвар :	доош → HMM: Feedback Essentials, Difficult Interactions
  Илтгэх чадвар :	доош → HMM: Presentation skill
  Бусдад хүлээн зөвшөөрөгдөх чадвар :	доош → HMM: Diversity, belonging and inclusion
  Стратегитай харилцах чадвар :	доош → HMM: Persuading Others, Global Collaboration

  МЕНЕЖМЕНТИЙН ЧАДАМЖ
  Үүрэг даалгаврыг оновчтой хуваарилах чадвар :	доош → HMM: Delegating
  Манлайлах чадвар :	доош → HMM: Leading People, Delegating
  Өөртөө итгэлтэй, шийдэмгий удирдах чадвар :	доош → HMM: Leading People, Decision Making
  Менторлох чадвар :	доош → HMM: Coaching, Developing Employees
  Гүйцэтгэлийг удирдах чадвар :	доош → HMM: Performance Measurement, Coaching
  Өөрчлөлтийг дэмжих чадвар :	доош → HMM: Change Management, Resilience

  СТРАТЕГИТАЙГААР АЖЛАА ТӨЛӨВЛӨЖ, ҮНЭЛЭХ ЧАДАМЖ
  Хямралыг удирдах чадвар :	доош → HMM: Crisis Management
  Далайцтай сэтгэх чадвар :	доош → HMM: Strategic Thinking
  Бүтээлч, шинэлэг сэтгэлгээтэй байх чадвар :	доош → HMM: Innovation, Strategic Thinking
  Олон үүрэг даалгаврыг зэрэг гүйцэтгэх чадвар :	доош → HMM: Time Management
  Зохион байгуулах чадвар :	доош → HMM: Process Improvement, Performance Measurement
  Төслийг удирдах чадвар :	доош → HMM: Project Management
  Эрсдэлийг удирдах чадвар :	доош → HMM: Crisis Management, Decision Making
  Стратеги төлөвлөгөө боловсруулах чадвар :	доош → HMM: Strategic Thinking, Strategy Planning, Innovation
  Цагийн менежмент :	доош → HMM: Time Management, Project Management, Managing Up
  Уран сайхны мэдрэмж :	доош → HMM: Innovation

  ХАРИЛЦАА ХОЛБОО ТОГТООЖ, УДИРДАХ ЧАДАМЖ
  Маргааныг шийдвэрлэх чадвар :	доош → HMM: Difficult Interactions, Negotiating
  Бусдыг ойлгох чадвар :	доош → HMM: Diversity, belonging and inclusion, Feedback Essentials
  Харилцаа тогтоох чадвар :	доош → HMM: Communication, Global Collaboration
  Багийн уур амьсгалыг бүрдүүлэх чадвар :	доош → HMM: Team Management
  Бусдад туслах :	доош → HMM: Team Management, Coaching
  Багийг идэвхижүүлэх чадвар :	доош → HMM: Team Management, Leading People

  ӨӨРИЙГӨӨ ТАНЬЖ МЭДЭХ, УДИРДАХ ЧАДАМЖ
  Дүрэм журам зааварчилгааг баримтлах чадвар :	доош → HMM: Ethic at work
  Өөрчлөлтөд хурдан зохицох чадвар :	доош → HMM: Change Management, Resilience
  Өөрийгөө бодитоор үнэлэх чадвар :	доош → HMM: Career management, Feedback Essentials
  Стрессээ удирдах чадвар :	доош → HMM: Stress management

  БАЙГУУЛЛАГЫН ҮНЭТ ЗҮЙЛ, СОЁЛ, ЭЕРЭГ УУР АМЬСГАЛЫГ БҮРДҮҮЛЭХ ЧАДАМЖ
  Мэдээллийн нууцыг хадгалах чадвар :	доош → HMM: Ethic at work
  Зорилтод шалгуурын дагуу шударга шийдвэр гаргах чадвар :	доош → HMM: Ethic at work, Decision Making
  Даруу төлөв байх чадвар :	доош → HMM: Ethic at work
  Бусдын соёл, ялгаатай байдлыг ойлгох чадвар :	доош → HMM: Diversity, belonging and inclusion
  Бусдыг хүндэтгэх чадвар :	доош → HMM: Diversity, belonging and inclusion
  Үүрэг хариуцлагаа ухамсарлах чадвар :	доош → HMM: Ethic at work

  АЖИЛДАА ХАНДАХ ХАНДЛАГА, ОРОЛЦООГ ТОДОРХОЙЛОХ ЧАДАМЖ
  Туслахад хэзээд бэлэн байх чадвар :	доош → HMM: Managing Your Boss, Team Management
  Санаачилгатай байх чадвар :	доош → HMM: Innovation
  Идэвхи, оролцоотой байх чадвар :	доош → HMM: Team Management
  Чанарыг эрхэмлэдэг :	доош → HMM: Process Improvement
  Хичээл зүтгэл гаргах чадвар :	доош → HMM: Goal Setting
  Хариуцлага хүлээх чадвар :	доош → HMM: Ethic at work, Performance Measurement

1. CTPI Менежерийн тест → ОПТИМАЛ МУЖ (10):

2. Big5 — Хувь хүний зан төлвийн тест (5 хэмжээс, оноо 1-10 оноогоор үнэлэж дүгнэлт гаргана.10 бол сайн 1 бол муу )
   Нээлттэй байдал (Openness-Imagination): 
   Нягт нямбай байдал (Meticulousness): 
   Нийтэч эрч хүчтэй байдал (Sociability-Dynamism):  ← "нийтэч" гэж бич!
   Бусдад анхаарал хандуулах байдал (Consciousness of Others): 
   
  

3. PP Test — Ажилтны хандлага, ур чадварын тест (16 шинж, оноо 1-10 оноогоор үнэлэж дүгнэлт гаргана. 10 бол сайн 1 бол муу)
   PP нь ажилтны хандлага ур чадварыг тодорхойлох тест юм.
   Баримтад тулгуурладаг (Focus on Facts): 
   Удирдан чиглүүлэх дуртай (Desire to Lead): 
   Сэтгэл хөдлөлөө хянадаг (Emotional Distance): 
   Амжилтанд хүрэх эрмэлзэлтэй (Ambition): 
   Шинийг эрэлхийлэгч (Novelty Seeking): 
   Нээлттэй харилцдаг (Extraversion): 
   Уян хатан байх чадвар (Flexibility): 
   Ажлыг нэн тэргүүнд тавьдаг (Involvement at Work): 
   Дүрэм журмыг дагадаг (Rule-Following): 
   Ятган нөлөөлдөг (Persuasiveness): 
   Шуурхай гүйцэтгэх дуртай (Need for Action): 
   Аливааг бэлтгэлгүй хийж чаддаг (Improvisation): 
   Бие дааж ганцаараа ажиллах дуртай (Autonomy): 
   Бусдыг нэн тэргүүнд тавьдаг (Altruism): 

4. VOC — Ажил мэргэжил, карьерийн чиг баримжаа тодорхойлох тест (оноо 1-10 оноогоор үнэлэж дүгнэлт гаргана. 10 бол сайн 1 бол муу)
   Аливааг таньж мэдэх болон суралцах сонирхол (Intellectual Curiosity): 
   Шинжлэх ухаан болон технологи (Science & Technology)
   Манлайлал (Leadership): 
   Бусдад туслах дэмжих (Dedication to Others): 
   Ажил хэрэгч (Enterprising): 
   Системтэй зохион байгуулалттай (Methodical): 
   Тоо баримт мэдээлэл дээр ажиллах сонирхол (Interest in Data & Numbers): 

5. EQ — Сэтгэл хөдлөлийн оюун ухаан (оноо 1-10 оноогоор үнэлэж дүгнэлт гаргана. 10 бол сайн 1 бол муу)
   Өөрийн болон бусдын сэтгэл хөдлөлийг ойлгох, удирдах чадвар.
   Өндөр EQ = өндөр харилцааны чадвар.
   Өөрийгөө таних (Self knowledge): 
   Өөрийгөө үнэлэх (Self regard): 
   Өөртөө итгэлтэй байх (Self  confidence): 
   Өөдрөг үзэлтэй (Optimism): 
   Тэсвэр хатуужилтай (Resilience): 
   Өөртөө урам зориг өгөх (Self-Motivation): 
   Өөрийгөө хянах (Self-control): 
   Ялгаатай байдалд зохицох (Dealing with diversity): 
   Өөрийн үзэл бодолд тууштай (Assertiveness): 
   Сэтгэл хөдлөлөө илэрхийлэх чадвартай (Expressing emotions): 
   Бусдыг ойлгох чадвартай (Empathy): 
   Эвлэрүүлэн зуучлах чадвартай (Mediation): 
   Бусдад урам зориг өгөх чадвартай (Motivating-others): 
   Ухаалаг эв дүйтэй (Tactfulness): 
   Уян хатан (Flexibility): 

6. MOTIVATION+ — Ажлын сэдлийн тест (оноо 1-10 оноогоор үнэлэж дүгнэлт гаргана. 10 бол сайн 1 бол муу)
   Ажилтны ажлын байран дахь сэдэлжүүлэгч хүчин зүйл ба тэдгээрийн сэтгэл ханамжийн түвшинг тодорхойлох тест.
   Магтаал сайшаал хүртэх : 
   Өөрийн хязгаарыг сорих : 
   Суралцах хүсэл эрмэлзэл :
   Карьер хөгжлүүлэх боломж : 
   Өрсөлдөөн : 
   Идэвхтэй хөдөлгөөн : 
   Бие даан чөлөөтэй ажиллах орчин : 
   Баталгаат байдал: 
   Эрүүл тэнцвэртэй байдал : 
   Нийгмийн орчин : 
   Олон нийтэд үйлчлэх : 
   Бусдыг хөгжүүлэх : 
   Нөлөөлөх : 
   Санаагаа хуваалцах :

7. Sales Competency — Борлуулалтын чадамжийн тест (оноо 1-10 оноогоор үнэлэж дүгнэлт гаргана. 10 бол сайн 1 бол муу)
   Борлуулалтын 10 ур чадварыг үнэлнэ.
   Үйлчлүүлэгч харилцагчийг өөртөө татах чадвар :
   Эрэл хайгуул хийх чадвар : 
   Харилцаа холбоо тогтоох чадвар : 
   Хэрэглэгчийг сэтгэл ханамжтай байлгах чадвар : 
   Стратеги борлуулалт хийх чадвар : 
   Үр дүнг төсөөлүүлэн тайлбарлах чадвар : 
   Хэрэглэгчийн хэрэгцээг ойлгох чадвар :
   Хэлцэл тохиролцоог амжилттай дуусгах чадвар : 
   Бусдыг татах чадвар : 
   Өөрийгөө хянах чадвар : 
   Борлуулалтын овсгоо самбаа : 


═══════════════════════════════════════════════════
ХАРИУЛТ БИЧИХ НАРИЙВЧИЛСАН ЗАГВАР (ЗӨВХӨН ДЭЛГЭРЭНГҮЙ ГОРИМД: Excel/бүлгийн
шинжилгээ, сургалтын төлөвлөгөө, эсвэл хэрэглэгч тодорхой "дэлгэрэнгүй" гэж
хүссэн үед. Энгийн асуултад ЭДГЭЭР ЗАГВАРЫГ БҮРЭН АШИГЛАХГҮЙ, товч хариулна.)
═══════════════════════════════════════════════════

ТООН ОНОО ЗААВАЛ ХАРУУЛАХ ЖИШЭЭ:
  "Болдын Манлайлах чадвар: 45/100 Ур чадварын оноо 0-49 хооронд хөгжүүлэх шаардлагатай байна.
   Энэ нь Harvard ManageMentor хөтөлбөрийн Leading People, Delegating модулиудыг
   нэн тэргүүнд судлах шаардлагатайг харуулж байна."

БҮЛГИЙН ШИНЖИЛГЭЭНИЙ ЗАГВАР (дэлгэрэнгүй горимд):
  1. Дундаж оноо
  2. Хамгийн өндөр оноотой 1-3 ажилтан (давуу тал)
  3. Хамгийн бага оноотой 1-3 ажилтан (хөгжүүлэх хэрэгтэй)
  4. Хамгийн сул болон хамгийн хүчтэй ур чадварын чиглэл


СУРГАЛТЫН ТӨЛӨВЛӨГӨӨ ЗАГВАР (дэлгэрэнгүй горимд):
  1-р АЛХАМ: Ажилтан бүрийн оноог оптималтай харьцуулж тодорхойл
  2-р АЛХАМ: Тохирох HMM модулиудыг нэрлэ
  3-р АЛХАМ: 1-р сар (яаралтай) → 2-3-р сар (дунд) → 4-6-р сар (урт хугацаа)
  4-р АЛХАМ: Хүлээгдэж буй үр дүн

НЭР ТОМЬЁОНЫ ЗӨВ ХЭРЭГЛЭЭ:
  туршилт → тест | психометрийн → сэтгэл зүйн | үр бүтээл → бүтээмж
  зохицол өндөртэй → уялдаа сайтай | удирдамжийн → удирдлагын
  эергээр → эерэгээр | нэр дэвшигч → ажил горилогч
  нийтэч (зөв) ← нийрч/ниймч (буруу)

"""


def build_system_prompt(state: AgentState, graphrag_context: str = "") -> str:
    prompt = (
        "Та бол ХИЙМЭЛ ОЮУН УХААНТ ЗӨВЛӨХ — 'Таlent AI' юм.\n\n"
        "🔴 ХАМГИЙН ЧУХАЛ ДҮРЭМ:\n"
        "- 'Мэдээлэл хангалтгүй', 'холбогдоно уу', 'одоогоор байхгүй' гэж ХЭЗЭЭ Ч хариулж болохгүй.\n"
        "- Өмнөх чатны түүхэнд тестийн оноо, шинжилгээ байвал ЗААВАЛ тэр дээр үндэслэн хариулна.\n"
        "- Chat history хоосон байвал ч HMM мэдлэгийн санд үндэслэн хариулна.\n"
        "- Follow-up асуулт ирэхэд өмнөх бүх хариулт дээр нэмж хариулна.\n\n"
        "🔵 МЭНДЧИЛГЭЭ, ӨӨРИЙГӨӨ ТАНИЛЦУУЛАХ ДҮРЭМ:\n"
        "- Хэрэглэгчийн бүхэл мессеж ЗӨВХӨН мэндчилгээ байвал ('сайн байна уу' гэх мэт, өөр асуулт ороогүй) эсвэл 'та хэн бэ'/'чи хэн бэ' гэж асуувал: ердийн 'Сайн байна уу' гэсэн ерөнхий мэндчилгээгээр ХАРИУЛАХГҮЙ. Үүний оронд яг үнэн зөв 'Хүний нөөцийн хиймэл оюун ухаант туслах Talent AI байна.' гэж өөрийгөө танилцуулж, дараа нь юугаар туслах талаар 1 өгүүлбэрээр товч асууна.\n"
        "- Хэрэглэгч мэндлээд ШУУД тодорхой асуулт ч асуувал (жишээ нь: 'Сайн байна уу, CTPI гэж юу вэ?') зөвхөн асуултад нь товч, шууд хариулна — өөрийгөө танилцуулах өгүүлбэрийг НЭМЖ БИЧИХГҮЙ.\n"
        "- Аль хэдийн чатны түүхэнд танилцуулга хийсэн бол дараагийн ямар ч асуултад дахин танилцуулгагүйгээр шууд хариулна.\n\n"
        "🟢 ХАРИУЛТЫН ТОВЧ, ТОДОРХОЙ БАЙДЛЫН ДҮРЭМ:\n"
        "- Хариултаа товч, тодорхой, шууд утгатай бич. Шаардлагагүй урт эссэ, давхардсан тайлбар, 'ус зутгах' маягийн өгүүлбэр огт бичихгүй.\n"
        "- Энгийн, ерөнхий асуултад (жишээ нь: 'CTPI гэж юу вэ?', нэг ур чадварын тайлбар) 2-5 өгүүлбэрт багтаан, 150-200 үгээс ХЭТРЭХГҮЙгээр хариулна.\n"
        "- Доорх 'БҮЛГИЙН ШИНЖИЛГЭЭНИЙ ЗАГВАР' болон 'СУРГАЛТЫН ТӨЛӨВЛӨГӨӨ ЗАГВАР'-ын дэлгэрэнгүй бүтцийг ЗӨВХӨН дараах тохиолдолд бүрэн ашиглана: (а) хэрэглэгч Excel/бүлгийн өгөгдөл оруулж дүн шинжилгээ хүссэн, (б) сургалтын төлөвлөгөө тусгайлан хүссэн, эсвэл (в) хэрэглэгч 'дэлгэрэнгүй', 'бүрэн задаргаа', 'дэлгэрэнгүй тайлбарла' гэж шууд хүссэн үед. Бусад бүх тохиолдолд товчхон хариулна.\n\n"
        "🟡 ФОРМАТЫН ДҮРЭМ:\n"
        "- Зөвхөн МОНГОЛ хэлээр, урсгал өгүүлбэрээр бич.\n"
        "- Зураас (-), од (*), тоон жагсаалт ашиглахгүй.\n"
        "- Тестийн нэрсийг **CTPI**, **PP**, **Big5** тодоор тэмдэглэ.\n"
        "- HMM модулийн нэрийг 'Harvard ManageMentor хөтөлбөрийн [нэр]' гэж дурд.\n"
        "- Оптимал мужид байгаа оноог сул тал гэж хэлж болохгүй.\n"
        "- ТООН ОНОО ЗААВАЛ ХАРУУЛАХ: ажилтан бүрийн оноог тодорхой дурдана. Жишээ: 'Манлайлах чадвар: 45/100 (оптимал: 65+)'. Оноо оптимал мужаас хэр зайтайг тайлбарла.\n"
        "- Бүлгийн шинжилгээнд: дундаж оноо, хамгийн өндөр/бага оноотой ажилтан, хамгийн сул болон хамгийн хүчтэй чиглэлийг тодорхойл (дэлгэрэнгүй горимд).\n\n"
        + HMM_KNOWLEDGE
    )
    if state.has_excel and state.excel_stats:
        _all_t = ["CTPI","Big5","PP","VOC","EQ","MOTIVATION+","Sales Competency"]
        _dt = state.detected_test_name or ""
        _dtl = [x.strip() for x in re.split(r"[,\s]+", _dt) if x.strip()]
        _forb = [t for t in _all_t if not any(t.lower() in d.lower() for d in _dtl)] if _dtl else []
        _eg = ("\n[EXCEL PROMPT GUARD]\n"
               + "Энэ файлд ЗӨВХӨН: " + (_dt or "?") + "\n"
               + ("ХОРИГЛОНО: " + ", ".join(_forb) + "\n" if _forb else "")
               + "Хориглосон тестийн нэрийг НЭГ Ч УДАА дурдахгүй.\n") if _dtl else ""
        prompt += "\n\n📊 EXCEL ӨГӨГДЛИЙН ХУРААНГУЙ (ЭНЭ ДЭЭР ҮНДЭСЛЭН ХАРИУЛ):\n" + state.excel_stats + "\n" + _eg
    if graphrag_context:
        prompt += f"\n\n📚 НЭМЭЛТ МЭДЛЭГ (GraphRAG):\n{graphrag_context}\n"
    return prompt


# ═══════════════════════════════════════════════════════════════════════════
#  GEMINI HELPERS (google-genai SDK)
# ═══════════════════════════════════════════════════════════════════════════

async def gemini_chat(system_prompt: str, history: List[Dict], user_message: str) -> str:
    """
    google-genai SDK-р multi-turn chat дуудна.
    history: [{"role": "user"|"model", "parts": ["text"]}]
    """
    client = get_genai_client()

    # History-г types.Content list болгоно
    contents = []
    for msg in history:
        role = msg.get("role", "user")
        parts = msg.get("parts", [])
        contents.append(
            types.Content(
                role=role,
                parts=[types.Part(text=p) for p in parts if p],
            )
        )
    # Одоогийн хэрэглэгчийн асуулт нэм
    contents.append(
        types.Content(role="user", parts=[types.Part(text=user_message)])
    )

    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.7,
        max_output_tokens=2048,
    )

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=contents,
        config=config,
    )
    return response.text or ""


async def gemini_simple(system_prompt: str, user_message: str) -> str:
    """Энгийн нэг удаагийн Gemini дуудалт."""
    client = get_genai_client()
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.5,
        max_output_tokens=2048,
    )
    response = await asyncio.to_thread(
        client.models.generate_content,
        model=GEMINI_MODEL,
        contents=user_message,
        config=config,
    )
    return response.text or ""


# ═══════════════════════════════════════════════════════════════════════════
#  AGENT 1 — Router
# ═══════════════════════════════════════════════════════════════════════════

def agent_router(state: AgentState) -> AgentState:
    try:
        session = get_session(state.session_id)
    except Exception as exc:
        logger.warning("[Agent 1] DB lookup failed: %s", exc)
        return state.model_copy(update={"has_excel": False})

    if not session:
        return state.model_copy(update={"has_excel": False})

    d = dict(session)
    excel_stats = d.get("summary")
    detected_test = d.get("test_name")
    if not excel_stats:
        return state.model_copy(update={"has_excel": False})

    return state.model_copy(update={
        "has_excel": True,
        "excel_stats": excel_stats,
        "detected_test_name": detected_test,
    })


# ═══════════════════════════════════════════════════════════════════════════
#  AGENT 2 — Psychometric Expert
# ═══════════════════════════════════════════════════════════════════════════

async def agent_psychometric_expert(state: AgentState) -> AgentState:
    logger.info("[Agent 2] Processing (history=%d msgs)...", len(state.chat_history))

    # GraphRAG — local_search index-ийг ЗААВАЛ оролдоно. Алдаа гарвал (индекс
    # байхгүй, embedding/completion дуудалт унасан гэх мэт) Gemini индексгүйгээр
    # хариулдаг руу шилждэг тул уг алдааг ЯГ ЮУ ГЭДГИЙГ нь ERROR түвшинд бүрэн
    # traceback-тай нь бүртгэнэ — эс тэгвэл индекс чимээгүйхэн алгасагдсаныг
    # хэн ч анзаарахгүй өнгөрдөг (жишээ нь litellm.aembedding алдаа).
    graphrag_context = ""
    graphrag_used = False
    try:
        prompt_lower = state.user_prompt.lower()
        follow_kw = ["тус", "дээрх", "төлөвлөгөө", "үүнээс", "дараагийн",
                     "сургалт", "хөгжил", "зөвлөмж", "өгнө үү", "гаргаж"]
        is_follow_up = any(kw in prompt_lower for kw in follow_kw) and len(state.chat_history) > 0
        _dtq = state.detected_test_name or "Central Test"
        query = f"{_dtq} сургалтын төлөвлөгөө хөгжлийн арга" if is_follow_up else f"{state.user_prompt}\n{state.excel_stats or ''}"

        def _run_graphrag_search(q: str):
            # GraphRAG (asyncio.run дотроо ашигладаг тул) uvicorn-ы uvloop event
            # loop дотор биш, тусдаа thread-д асаах ёстой — эс тэгвэл
            # "Can't patch loop of type uvloop.Loop" алдаа гарна.
            engine = get_local_search_engine()
            return engine.search(q)

        result = await asyncio.to_thread(_run_graphrag_search, query)
        raw_ctx = result.response if hasattr(result, "response") else str(result)
        BAD = ["хангалттай байхгүй байна", "холбогдоно уу", "мэдээлэл одоогоор", "insufficient"]
        if any(p in raw_ctx for p in BAD):
            logger.info("[Agent 2] GraphRAG local_search ran but reported insufficient context.")
        else:
            graphrag_context = raw_ctx
            graphrag_used = True
    except Exception:
        logger.error(
            "[Agent 2] GraphRAG local_search FAILED — Gemini will answer WITHOUT the "
            "indexed knowledge base for this request (unindexed fallback).",
            exc_info=True,
        )

    logger.info(
        "[Agent 2] GraphRAG index %s (context_chars=%d)",
        "USED" if graphrag_used else "BYPASSED",
        len(graphrag_context),
    )

    system_prompt = build_system_prompt(state, graphrag_context)

    # DB history → Gemini format
    gemini_history = []
    for msg in state.chat_history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        gemini_history.append({
            "role": "user" if role == "user" else "model",
            "parts": [content],
        })

    logger.info("[Agent 2] Gemini call with %d history turns", len(gemini_history))

    raw_analysis = ""
    had_error = False
    try:
        raw_analysis = await gemini_chat(system_prompt, gemini_history, state.user_prompt)
        logger.info("[Agent 2] Gemini responded (%d chars)", len(raw_analysis))
    except Exception as exc:
        logger.error("[Agent 2] Gemini failed: %s", exc)
        raw_analysis = ERROR_FALLBACK_TEXT
        had_error = True

    return state.model_copy(update={
        "raw_analysis": raw_analysis, "graphrag_context": graphrag_context,
        "graphrag_used": graphrag_used, "had_error": had_error,
    })


# ═══════════════════════════════════════════════════════════════════════════
#  AGENT 3 — Formatter
# ═══════════════════════════════════════════════════════════════════════════

_NEEDS_FORMATTING_RE = re.compile(r"^[ \t]*[-*+]\s|^[ \t]*\d+[.)]\s|\|.*?\|", re.MULTILINE)


def _needs_formatting_pass(text: str) -> bool:
    """Agent 2-ийн системийн prompt өөрөө markdown/bullet-гүй, товч урсгал
    өгүүлбэрээр бичихийг ЗААВАЛ шаарддаг тул ихэнх хариулт нэмэлт LLM
    дуудалт (Agent 3) шаардлагагүй байдаг — apply_mongolian_regex_fixes()
    механикаар цэвэрлэхэд хангалттай. Зөвхөн бодит markdown жагсаалт/хүснэгт
    эсвэл тасарсан хариулт илэрсэн үед л (ховор тохиолдол) нэмэлт
    LLM-ээр дахин бичүүлж, секунд тутамд нэмэлт 5-10 секунд шаардах
    дэмий дуудалтаас зайлсхийнэ."""
    return bool(_NEEDS_FORMATTING_RE.search(text)) or "[Үргэлжилсэн" in text


async def agent_critic_formatter(state: AgentState) -> AgentState:
    if not state.raw_analysis:
        return state.model_copy(update={"final_output": "Шинжилгээний үр дүн олдсонгүй.", "had_error": True})

    if not _needs_formatting_pass(state.raw_analysis) and len(state.raw_analysis) > 20:
        return state.model_copy(update={"final_output": apply_mongolian_regex_fixes(state.raw_analysis)})

    data_scope = "Бүлгийн дүн шинжилгээ (Excel)" if state.has_excel else "Хувь хүний оноо"
    test_names = state.detected_test_name or "Central Test"

    formatter_sys = (
        "You are a Mongolian content formatter for Talent AI. "
        "Output the analysis faithfully in Mongolian flowing paragraphs. "
        "No placeholders. No bullets, dashes, numbered lists. "
        "Keep all Harvard ManageMentor references exactly. "
        "Be concise and direct — do not pad with filler or restate the same point. "
        "Do not expand a short answer into a longer one; keep it under 150-200 words "
        "unless the original analysis is an explicit detailed group/Excel breakdown "
        "or training plan, in which case preserve its full detail.\n"
    )

    formatted = ""
    try:
        formatted = await gemini_simple(formatter_sys, state.raw_analysis)
    except Exception as exc:
        logger.error("[Agent 3] Failed: %s", exc)
        formatted = state.raw_analysis

    if "[Үргэлжилсэн" in formatted or len(formatted) < 50:
        formatted = state.raw_analysis

    return state.model_copy(update={"final_output": apply_mongolian_regex_fixes(formatted)})


# ═══════════════════════════════════════════════════════════════════════════
#  PIPELINE
# ═══════════════════════════════════════════════════════════════════════════

async def run_pipeline(session_id: str, user_message: str) -> str:
    try:
        raw_history = get_messages(session_id, limit=30)
    except Exception:
        raw_history = []

    cleaned_history = [
        {"role": m.get("role", "user"), "content": m.get("content", "")}
        for m in raw_history if m.get("content")
    ]

    state = AgentState(
        session_id=session_id,
        user_prompt=user_message,
        chat_history=cleaned_history,
    )

    state = agent_router(state)
    state = await agent_psychometric_expert(state)
    state = await agent_critic_formatter(state)

    final = state.final_output or ""

    # ҮРГЭЛЖ хадгална (техникийн алдааны хариултыг чатны түүхэнд хадгалахгүй —
    # эс тэгвэл дараагийн асуултад Gemini энэ "алдаа" дээр үндэслэн хариулж эхэлдэг.
    # had_error flag-аар шалгах нь Agent 3 алдааны текстийг дахин найруулсан ч
    # алгасахгүй үлдэхээс сэргийлнэ, exact string match-аас илүү найдвартай.)
    save_message(session_id, "user", user_message)
    if final and len(final) > 10 and not state.had_error:
        save_message(session_id, "assistant", final)

    return final


# ═══════════════════════════════════════════════════════════════════════════
#  FASTAPI
# ═══════════════════════════════════════════════════════════════════════════

app = FastAPI(title="Talent AI API", version="2.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # "*" origins-тай хамт True тавих боломжгүй (browser хориглоно)
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    init_db()
    logger.info("Talent AI API v2.5.0 started")

def resolve_session_id(body_sid: Optional[str], request: Request) -> str:
    return (body_sid or "").strip() or request.headers.get("X-Session-Id", "").strip()


class AskRequest(BaseModel):
    session_id: Optional[str] = None
    message: Optional[str] = None
    prompt: Optional[str] = None  # хуучин frontend нийцэл

class AskResponse(BaseModel):
    answer: str

@app.post("/ask", response_model=AskResponse)
async def ask_endpoint(request: Request, body: AskRequest):
    session_id = resolve_session_id(body.session_id, request)
    user_message = (body.message or body.prompt or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id шаардлагатай.")
    if not user_message:
        raise HTTPException(status_code=400, detail="message шаардлагатай.")
    try:
        answer = await run_pipeline(session_id, user_message)
    except Exception as exc:
        logger.exception("Pipeline crashed: %s", exc)
        raise HTTPException(status_code=500, detail="Шинжилгээний системд алдаа гарлаа.")
    return AskResponse(answer=answer)


@app.post("/analyze-excel")
async def analyze_excel_endpoint(
    request: Request,
    file: UploadFile = File(...),
    question: str = Form(default="Энэ өгөгдлийг дүн шинжилгээ хийж дүгнэлт гарга"),
    session_id: Optional[str] = Form(default=None),
):
    sid = resolve_session_id(session_id, request)
    if not sid:
        raise HTTPException(status_code=400, detail="session_id шаардлагатай.")
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Зөвхөн .xlsx, .xls, .csv файл зөвшөөрнө.")
    try:
        file_bytes = await file.read()
        result = process_excel(file_bytes, file.filename, question=question)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Файл боловсруулахад алдаа: {exc}")

    detected_tests = result.get("detected_tests", [])
    primary_test = detected_tests[0] if detected_tests else "Тодорхойгүй"
    update_session_file(
        session_id=sid, filename=file.filename,
        summary=result.get("summary", ""), test_name=primary_test,
        rows=result.get("rows", 0), columns=result.get("columns", []),
        raw_data=result.get("raw_data", []),
    )
    try:
        answer = await run_pipeline(sid, question)
    except Exception as exc:
        logger.exception("Pipeline crashed after excel upload: %s", exc)
        answer = "Файл хадгалагдлаа. Дараагийн асуултаа бичнэ үү."
    return {"status": "processed", "session_id": sid, "test_name": primary_test, "answer": answer}


@app.post("/upload-excel")
async def upload_excel_endpoint(
    request: Request,
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(default=None),
):
    sid = resolve_session_id(session_id, request)
    if not sid:
        raise HTTPException(status_code=400, detail="session_id шаардлагатай.")
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls", ".csv")):
        raise HTTPException(status_code=400, detail="Unsupported format.")
    try:
        file_bytes = await file.read()
        result = process_excel(file_bytes, file.filename, question="")
        detected_tests = result.get("detected_tests", [])
        primary_test = detected_tests[0] if detected_tests else "Тодорхойгүй"
        update_session_file(
            session_id=sid, filename=file.filename,
            summary=result.get("summary", ""), test_name=primary_test,
            rows=result.get("rows", 0), columns=result.get("columns", []),
            raw_data=result.get("raw_data", []),
        )
        return {"status": "processed", "session_id": sid, "test_name": primary_test}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/sessions")
async def create_session_endpoint():
    return {"session_id": create_session()}

@app.get("/sessions")
async def list_sessions_endpoint():
    return {"sessions": get_all_sessions()}

@app.delete("/sessions/{session_id}")
async def delete_session_endpoint(session_id: str):
    delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}

@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "2.5.0"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main_api:app", host="0.0.0.0", port=8000, reload=True)
