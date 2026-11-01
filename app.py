import hashlib
import json
from datetime import datetime

import streamlit as st
from openai import OpenAI
from pypdf import PdfReader
from streamlit.errors import StreamlitSecretNotFoundError
from supabase import Client, create_client


QUIZ_SYSTEM_PROMPT = """당신은 학습 퀴즈를 만드는 도우미입니다.
주어진 요약을 바탕으로 4지선다 퀴즈 3문항을 만드세요.
각 문항은 question, options(4개), answer(정답 인덱스, 0부터 시작), explanation을 포함해야 합니다.
결과는 {"questions": [...]} 형태의 JSON으로 반환하세요."""


@st.cache_resource
def get_supabase_client(url: str, key: str) -> Client:
    return create_client(url, key)


def get_summary_preview(summary: dict, max_length: int = 160) -> str:
    sentences = summary.get("summary", [])
    text = " ".join(str(sentence) for sentence in sentences)
    if len(text) <= max_length:
        return text
    return f"{text[:max_length].rstrip()}..."


def format_created_at(value: str) -> str:
    try:
        created_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return created_at.astimezone().strftime("%Y-%m-%d %H:%M")
    except (AttributeError, ValueError):
        return value


st.set_page_config(page_title="AI 학습 정리 노트", page_icon="📝", layout="wide")

st.title("AI 학습 정리 노트")
st.caption("학습할 PDF를 업로드하면 문서의 텍스트를 추출해 보여드립니다.")

with st.sidebar:
    st.header("문서")
    uploaded_file = st.file_uploader(
        "PDF 파일 업로드",
        type=["pdf"],
        help="PDF 형식의 파일만 업로드할 수 있습니다.",
    )

original_tab, summary_tab, quiz_tab, history_tab = st.tabs(
    ["원문", "요약", "퀴즈", "저장 기록"]
)
st.session_state.setdefault("summaries", {})
st.session_state.setdefault("quizzes", {})
st.session_state.setdefault("saved_summary_ids", {})

try:
    openai_api_key = st.secrets["OPENAI_API_KEY"]
except (StreamlitSecretNotFoundError, KeyError):
    openai_api_key = None

try:
    supabase_url = st.secrets["SUPABASE_URL"]
    supabase_key = st.secrets["SUPABASE_KEY"]
except (StreamlitSecretNotFoundError, KeyError):
    supabase_url = None
    supabase_key = None

supabase_client = (
    get_supabase_client(supabase_url, supabase_key)
    if supabase_url and supabase_key
    else None
)

if uploaded_file is not None:
    try:
        document_id = hashlib.sha256(uploaded_file.getvalue()).hexdigest()
        reader = PdfReader(uploaded_file)
        page_count = len(reader.pages)
        extracted_pages = [page.extract_text() or "" for page in reader.pages]
        document_text = "\n\n".join(
            f"{page_number}페이지\n{page_text.strip()}"
            for page_number, page_text in enumerate(extracted_pages, start=1)
            if page_text.strip()
        )
        summary = st.session_state["summaries"].get(document_id)

        with st.sidebar:
            st.subheader("문서 정보")
            st.caption("파일명")
            st.write(uploaded_file.name)
            st.metric("총 페이지 수", page_count)

        with original_tab:
            for page_number, page_text in enumerate(extracted_pages, start=1):
                with st.expander(f"{page_number}페이지", expanded=page_number == 1):
                    if page_text.strip():
                        st.text(page_text.strip())
                    else:
                        st.info("이 페이지에서 추출할 수 있는 텍스트를 찾지 못했습니다.")

        with summary_tab:
            if not document_text:
                st.info("요약할 수 있는 텍스트가 없습니다.")
            else:
                if not openai_api_key:
                    st.warning(
                        ".streamlit/secrets.toml에 OPENAI_API_KEY를 설정해 주세요."
                    )

                if st.button(
                    "요약하기",
                    type="primary",
                    disabled=not openai_api_key,
                ):
                    try:
                        client = OpenAI(api_key=openai_api_key)
                        response = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {
                                    "role": "system",
                                    "content": (
                                        "사용자가 제공한 문서를 분석해 반드시 JSON으로만 응답하세요. "
                                        "JSON은 summary(핵심 요약 3문장 배열), "
                                        "keywords(주요 키워드 5개 배열), "
                                        "difficulty(난이도: 상, 중, 하 중 하나) 필드를 포함해야 합니다."
                                    ),
                                },
                                {"role": "user", "content": document_text},
                            ],
                            response_format={"type": "json_object"},
                        )
                        response_content = response.choices[0].message.content
                        if response_content is None:
                            raise ValueError("요약 응답이 비어 있습니다.")
                        st.session_state["summaries"][document_id] = json.loads(
                            response_content
                        )
                    except Exception as error:
                        st.error(f"요약을 생성하는 중 오류가 발생했습니다: {error}")

                summary = st.session_state["summaries"].get(document_id)
                if summary:
                    st.subheader("핵심 요약")
                    for sentence in summary.get("summary", []):
                        st.write(sentence)

                    st.subheader("주요 키워드")
                    st.write(", ".join(summary.get("keywords", [])))

                    st.subheader("난이도")
                    st.write(summary.get("difficulty", "-"))

                    if st.button("저장", icon=":material/save:"):
                        if supabase_client is None:
                            st.error(
                                "secrets.toml에 SUPABASE_URL과 SUPABASE_KEY를 "
                                "설정해 주세요."
                            )
                        else:
                            try:
                                saved_records = (
                                    supabase_client.table("summaries")
                                    .insert(
                                        {
                                            "filename": uploaded_file.name,
                                            "summary": summary,
                                        }
                                    )
                                    .execute()
                                    .data
                                )
                                if not saved_records:
                                    raise ValueError("저장된 행의 ID를 확인할 수 없습니다.")
                                st.session_state["saved_summary_ids"][document_id] = (
                                    saved_records[0]["id"]
                                )
                                st.success("요약을 저장했습니다.")
                            except Exception as error:
                                st.error(f"요약을 저장하는 중 오류가 발생했습니다: {error}")

        with quiz_tab:
            if summary is None:
                st.info("퀴즈를 만들려면 요약을 먼저 생성해 주세요.")
            else:
                saved_summary_id = st.session_state["saved_summary_ids"].get(
                    document_id
                )

                if saved_summary_id is None:
                    st.info("퀴즈를 만들기 전에 요약을 먼저 저장해주세요.")
                elif not openai_api_key:
                    st.warning(
                        ".streamlit/secrets.toml에 OPENAI_API_KEY를 설정해 주세요."
                    )
                elif st.button("퀴즈 만들기", type="primary"):
                    try:
                        client = OpenAI(api_key=openai_api_key)
                        response = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {"role": "system", "content": QUIZ_SYSTEM_PROMPT},
                                {
                                    "role": "user",
                                    "content": json.dumps(summary, ensure_ascii=False),
                                },
                            ],
                            response_format={"type": "json_object"},
                        )
                        response_content = response.choices[0].message.content
                        if response_content is None:
                            raise ValueError("퀴즈 응답이 비어 있습니다.")
                        quiz = json.loads(response_content)
                        questions = quiz.get("questions", [])
                        if len(questions) != 3:
                            raise ValueError("퀴즈 문항 수가 3개가 아닙니다.")

                        updated_records = (
                            supabase_client.table("summaries")
                            .update({"quiz": quiz})
                            .eq("id", saved_summary_id)
                            .execute()
                            .data
                        )
                        if not updated_records:
                            raise ValueError("저장된 요약을 찾을 수 없습니다.")

                        st.session_state["quizzes"][document_id] = quiz
                        st.success("퀴즈를 만들고 저장했습니다.")
                    except Exception as error:
                        st.error(f"퀴즈를 생성하는 중 오류가 발생했습니다: {error}")

                quiz = st.session_state["quizzes"].get(document_id)
                if quiz:
                    quiz_version = hashlib.sha256(
                        json.dumps(quiz, sort_keys=True).encode()
                    ).hexdigest()[:8]
                    for question_number, question in enumerate(
                        quiz.get("questions", []), start=1
                    ):
                        st.subheader(f"문항 {question_number}")
                        options = question.get("options", [])
                        selected_answer = st.radio(
                            question.get("question", ""),
                            options=range(len(options)),
                            format_func=lambda option_index, values=options: values[
                                option_index
                            ],
                            index=None,
                            key=(
                                f"quiz_answer_{document_id}_{quiz_version}_"
                                f"{question_number}"
                            ),
                        )
                        if selected_answer is not None:
                            correct_answer = question.get("answer")
                            if selected_answer == correct_answer:
                                st.success("정답입니다.")
                            else:
                                st.error(
                                    f"오답입니다. 정답: {options[correct_answer]}"
                                )
                            st.info(f"해설: {question.get('explanation', '')}")
                        st.divider()
    except Exception as error:
        with original_tab:
            st.error(f"PDF를 읽는 중 오류가 발생했습니다: {error}")
else:
    with original_tab:
        st.info("사이드바에서 PDF 파일을 업로드해 주세요.")
    with summary_tab:
        st.info("요약할 PDF 파일을 먼저 업로드해 주세요.")
    with quiz_tab:
        st.info("퀴즈를 만들 PDF 파일을 먼저 업로드해 주세요.")

with history_tab:
    if supabase_client is None:
        st.warning(
            "저장 기록을 사용하려면 secrets.toml에 "
            "SUPABASE_URL과 SUPABASE_KEY를 설정해 주세요."
        )
    else:
        try:
            records = (
                supabase_client.table("summaries")
                .select("id, filename, summary, created_at")
                .order("created_at", desc=True)
                .execute()
                .data
            )

            if not records:
                st.info("저장된 요약이 없습니다.")

            for record in records:
                details, action = st.columns([4, 1.5])
                with details:
                    st.markdown(f"**{record['filename']}**")
                    st.caption(format_created_at(record["created_at"]))
                    st.write(get_summary_preview(record["summary"]))
                with action:
                    if st.button(
                        "삭제",
                        key=f"delete_{record['id']}",
                        icon=":material/delete:",
                        width="stretch",
                    ):
                        supabase_client.table("summaries").delete().eq(
                            "id", record["id"]
                        ).execute()
                        for saved_document_id, saved_record_id in list(
                            st.session_state["saved_summary_ids"].items()
                        ):
                            if saved_record_id == record["id"]:
                                st.session_state["saved_summary_ids"].pop(
                                    saved_document_id
                                )
                                st.session_state["quizzes"].pop(
                                    saved_document_id, None
                                )
                        st.rerun()
                st.divider()
        except Exception as error:
            st.error(f"저장 기록을 불러오는 중 오류가 발생했습니다: {error}")