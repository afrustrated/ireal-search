import streamlit as st
import urllib.parse
import re
from pyRealParser import Tune

# ==========================================
# 0. 데이터 설정
# ==========================================
# 여기에 가지고 계신 긴 iReal Pro 데이터를 붙여넣으세요.
DEFAULT_DATA = "irealb://..." 

# ==========================================
# 1. 화성학 엔진 (Harmony Engine)
# ==========================================
class HarmonyEngine:
    def __init__(self):
        self.note_map = {
            'C': 0, 'B#': 0, 'Db': 1, 'C#': 1, 'D': 2, 'Eb': 3, 'D#': 3,
            'E': 4, 'Fb': 4, 'F': 5, 'E#': 5, 'Gb': 6, 'F#': 6, 'G': 7,
            'Ab': 8, 'G#': 8, 'A': 9, 'Bb': 10, 'A#': 10, 'B': 11, 'Cb': 11
        }
        # 역매핑 (숫자 -> 문자, 결과 출력용 아님)
        self.num_to_note = {v: k for k, v in self.note_map.items()}

    def simplify_quality(self, quality_str):
        """ 코드 성질 단순화 및 정규화 """
        q = quality_str.strip()
        q = q.replace("^", "maj").replace("-", "m")

        if q in ["", "6", "maj", "maj7", "M7", "M"]: return "MAJOR"
        if q in ["m", "m6", "min"]: return "MINOR"
        if q in ["dim", "dim7", "o", "o7", "0", "°", "diminished"]: return "DIM"
        if q in ["m7b5", "h", "h7", "ø", "Ø"]: return "HALF_DIM"
        if q in ["7", "9", "11", "13", "7alt", "7b9", "7#9"]: return "DOMINANT" # 도미넌트도 묶음 (선택)
        
        return q

    def parse_chord(self, chord_str):
        """ (근음, 단순화된 성질, 베이스음) 반환 """
        if not chord_str: return None, None, None
        
        if '/' in chord_str:
            main, bass = chord_str.split('/')[:2]
        else:
            main, bass = chord_str, None

        match = re.match(r"([A-G][b#]?)(.*)", main)
        if match:
            root = match.group(1)
            quality = self.simplify_quality(match.group(2))
            if not bass: bass = root
            return root, quality, bass
        return None, None, None

    def get_semitone_distance(self, note1, note2):
        """ note1에서 note2까지의 반음 거리 """
        if note1 not in self.note_map or note2 not in self.note_map: return None
        v1, v2 = self.note_map[note1], self.note_map[note2]
        return (v2 - v1) % 12
    
    def get_key_root(self, key_str):
        """ 키 문자열(Eb-, C 등)에서 근음 추출 """
        # iReal Pro 키는 'A-' 형태가 많음
        clean_key = key_str.replace('-', '').strip()
        return clean_key

# ==========================================
# 2. 데이터 처리 및 검색 로직
# ==========================================
@st.cache_data
def load_songs_from_string(ireal_string):
    decoded_string = urllib.parse.unquote(ireal_string)
    if decoded_string.startswith("irealb://"): decoded_string = decoded_string[9:]
    songs = []
    for raw_song in decoded_string.split("==="):
        if not raw_song.strip(): continue
        try:
            full_uri = "irealb://" + urllib.parse.quote(raw_song)
            parsed = Tune.parse_ireal_url(full_uri)
            if isinstance(parsed, list): songs.extend(parsed)
            else: songs.append(parsed)
        except: pass
    return songs

def extract_clean_chords(song):
    """ 곡의 코드 문자열을 리스트로 변환 """
    raw = song.chord_string.replace("-", "m").replace("^", "maj")
    clean = re.sub(r"[\|\[\]\{\}\(\)\*xT<>]", " ", raw)
    return [c for c in clean.split() if not c.isdigit()]

# --- [모드 1] 실음 코드 검색 (Absolute) ---
def search_absolute(songs, user_input_str, engine):
    found_songs = []
    user_chords = user_input_str.split()
    if not user_chords: return []

    # 사용자가 입력한 코드의 (Root, Quality, Bass) 구조체 생성
    target_dna = []
    for c in user_chords:
        r, q, b = engine.parse_chord(c)
        if not r: return []
        target_dna.append({"root": r, "quality": q, "bass": b})
    
    search_len = len(target_dna)

    for song in songs:
        try:
            song_chords = extract_clean_chords(song)
            if len(song_chords) < search_len: continue

            for i in range(len(song_chords) - search_len + 1):
                window = song_chords[i : i + search_len]
                match = True
                
                for j in range(search_len):
                    wr, wq, wb = engine.parse_chord(window[j])
                    tr, tq, tb = target_dna[j]['root'], target_dna[j]['quality'], target_dna[j]['bass']
                    
                    # 1. 근음(Root)이 정확히 같은가? (이명동음 처리 위해 숫자값 비교)
                    if engine.get_semitone_distance(tr, wr) != 0:
                        match = False; break
                    # 2. 성질(Quality)이 같은가?
                    if tq != wq:
                        match = False; break
                    # 3. 베이스(Bass)가 정확히 같은가?
                    if engine.get_semitone_distance(tb, wb) != 0:
                        match = False; break
                
                if match:
                    found_songs.append(song)
                    break
        except: continue
    return found_songs

# --- [모드 2] 화성적 기능 코드 검색 (Harmonic Function) ---
def search_harmonic_function(songs, user_input_str, context_key, engine):
    found_songs = []
    user_chords = user_input_str.split()
    if not user_chords: return []
    
    # Context Key 검증
    if context_key not in engine.note_map: return []

    # Target DNA: (Key로부터의 거리, Quality, Root-Bass 간격)
    target_dna = []
    for c in user_chords:
        r, q, b = engine.parse_chord(c)
        if not r: return []
        
        # Key 기준 Root의 도수 (예: Key C에서 Em -> 거리 4)
        degree_interval = engine.get_semitone_distance(context_key, r)
        # Bass Offset (예: C/E -> 거리 4)
        bass_offset = engine.get_semitone_distance(r, b)
        
        target_dna.append({
            "degree": degree_interval,
            "quality": q,
            "bass_offset": bass_offset
        })

    search_len = len(target_dna)

    for song in songs:
        try:
            # 곡의 Key 가져오기
            song_key_root = engine.get_key_root(song.key)
            if song_key_root not in engine.note_map: continue

            song_chords = extract_clean_chords(song)
            if len(song_chords) < search_len: continue

            for i in range(len(song_chords) - search_len + 1):
                window = song_chords[i : i + search_len]
                match = True

                for j in range(search_len):
                    wr, wq, wb = engine.parse_chord(window[j])
                    t = target_dna[j]

                    # 1. 도수(Degree) 비교: (곡의 Key ~ 코드 Root) == (사용자 Key ~ 사용자 Root)
                    current_degree = engine.get_semitone_distance(song_key_root, wr)
                    if current_degree != t["degree"]:
                        match = False; break
                    
                    # 2. Quality 비교
                    if wq != t["quality"]:
                        match = False; break

                    # 3. Bass Offset 비교
                    current_bass_offset = engine.get_semitone_distance(wr, wb)
                    if current_bass_offset != t["bass_offset"]:
                        match = False; break
                
                if match:
                    found_songs.append(song)
                    break
        except: continue
    return found_songs

# --- [모드 3] 상대적 인터벌 검색 (기존 기능) ---
def search_relative_interval(songs, user_input_str, engine):
    found_songs = []
    user_chords = user_input_str.split()
    if not user_chords: return []

    user_dna = []
    fr, fq, fb = engine.parse_chord(user_chords[0])
    if not fr: return []

    # 첫 코드 기준 상대 거리 저장
    for c in user_chords:
        r, q, b = engine.parse_chord(c)
        root_int = engine.get_semitone_distance(fr, r)
        bass_off = engine.get_semitone_distance(r, b)
        user_dna.append({"root_int": root_int, "quality": q, "bass_off": bass_off})
    
    search_len = len(user_dna)

    for song in songs:
        try:
            song_chords = extract_clean_chords(song)
            if len(song_chords) < search_len: continue
            
            for i in range(len(song_chords) - search_len + 1):
                window = song_chords[i : i + search_len]
                wfr, _, _ = engine.parse_chord(window[0]) # 윈도우 첫 코드 기준
                match = True
                for j in range(search_len):
                    wr, wq, wb = engine.parse_chord(window[j])
                    t = user_dna[j]
                    
                    if wq != t["quality"]: match = False; break
                    if engine.get_semitone_distance(wfr, wr) != t["root_int"]: match = False; break
                    if engine.get_semitone_distance(wr, wb) != t["bass_off"]: match = False; break
                
                if match: found_songs.append(song); break
        except: continue
    return found_songs

# ==========================================
# 3. UI 구성 (Streamlit)
# ==========================================
st.set_page_config(page_title="Jazz Chord Finder", layout="wide")
st.title("🎷 iReal Pro Chord Finder")
st.markdown("원하는 방식으로 재즈 스탠다드 곡을 검색하세요.")

# 데이터 로딩
if len(DEFAULT_DATA) < 50:
    st.error("⚠️ 코드 상단의 `DEFAULT_DATA` 변수에 데이터를 넣어주세요.")
    st.stop()
else:
    with st.spinner("데이터베이스 준비 중..."):
        song_db = load_songs_from_string(DEFAULT_DATA)
    st.success(f"📚 {len(song_db)}곡 로드 완료")

st.divider()

# --- 검색 모드 선택 ---
search_mode = st.radio(
    "검색 모드 선택",
    ("실음 코드 검색 (Real Note)", "화성적 기능 코드 검색 (Harmonic Function)", "상대적 인터벌 검색 (Interval)"),
    index=1,
    help="""
    - **실음 코드**: 입력한 코드 이름 그대로 검색합니다. (예: Dm7은 Dm7만 찾음)
    - **화성적 기능**: 설정한 키 내에서의 역할을 기준으로 검색합니다. (예: C키의 Em7 = 3도 마이너)
    - **상대적 인터벌**: 키와 상관없이 코드들 간의 간격 흐름만 봅니다.
    """
)

# --- 입력 UI ---
col1, col2, col3 = st.columns([1, 3, 1])
engine = HarmonyEngine()

with col1:
    # 화성적 기능 검색일 때만 '기준 키' 선택창 표시
    if "화성적 기능" in search_mode:
        key_options = ['C', 'Db', 'D', 'Eb', 'E', 'F', 'Gb', 'G', 'Ab', 'A', 'Bb', 'B']
        selected_key = st.selectbox("기준 키 (Key)", key_options, index=0)
    else:
        st.write("") # 빈 공간

with col2:
    input_placeholder = "예: Dm7 G7 Cmaj7"
    if "실음" in search_mode: input_placeholder = "예: Dm7 G7 (정확히 이 코드만 찾음)"
    elif "화성적" in search_mode: input_placeholder = f"예: Em7 A7 (Key {selected_key} 기준 3도-6도 진행)"
    
    search_input = st.text_input("코드 진행 입력", placeholder=input_placeholder)

with col3:
    st.write("")
    st.write("")
    run_btn = st.button("검색 🚀", use_container_width=True)

# --- 실행 로직 ---
if run_btn and search_input:
    results = []
    
    if "실음" in search_mode:
        st.caption(f"🔍 **Absolute Mode:** '{search_input}' 그대로 검색")
        results = search_absolute(song_db, search_input, engine)
        
    elif "화성적" in search_mode:
        st.caption(f"🔍 **Harmonic Mode:** Key {selected_key}에서 '{search_input}'의 역할로 검색")
        results = search_harmonic_function(song_db, search_input, selected_key, engine)
        
    else: # 상대적 인터벌
        st.caption(f"🔍 **Interval Mode:** '{search_input}'의 상대적 흐름으로 검색")
        results = search_relative_interval(song_db, search_input, engine)

    # 결과 출력
    st.subheader(f"결과: {len(results)}곡 발견")
    if results:
        # 결과 테이블 데이터 생성
        res_data = []
        for s in results:
            res_data.append({
                "Title": s.title,
                "Composer": s.composer,
                "Key": s.key,      # 곡의 원래 키
                "Style": s.style
            })
        st.dataframe(res_data, use_container_width=True)
    else:
        st.warning("조건에 맞는 곡을 찾지 못했습니다.")
