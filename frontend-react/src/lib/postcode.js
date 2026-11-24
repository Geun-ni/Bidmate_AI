/**
 * 본점 주소 검색: 다음(카카오) 우편번호 서비스 (키 없이 브라우저에서 동작).
 *
 * 백엔드는 행안부 도로명주소 팝업 결과의 시도·시군구 이름(juso.siNm, sggNm)을 받아 SRM 지역으로 바꾼다.
 * 행안부 팝업은 결과를 서버로 POST 해서 돌려주는 방식이라 정적 화면만으로는 받을 수 없어,
 * 같은 이름 체계(시도 정식 명칭 + 시군구)를 주는 다음 우편번호 결과를 그 모양으로 바꿔 보낸다.
 */
const SCRIPT = "https://t1.daumcdn.net/mapjsapi/bundle/postcode/prod/postcode.v2.js";

// 다음 우편번호는 시도를 '서울', '경기'처럼 줄여서 줄 때가 있어 정식 명칭으로 맞춘다
const SIDO_FULL = {
  서울: "서울특별시", 부산: "부산광역시", 대구: "대구광역시", 인천: "인천광역시", 광주: "광주광역시",
  대전: "대전광역시", 울산: "울산광역시", 세종: "세종특별자치시", 세종시: "세종특별자치시", 경기: "경기도",
  강원: "강원특별자치도", 충북: "충청북도", 충남: "충청남도", 전북: "전북특별자치도", 전남: "전라남도",
  경북: "경상북도", 경남: "경상남도", 제주: "제주특별자치도",
};

let loading = null;
function loadScript() {
  if (window.daum?.Postcode) return Promise.resolve();
  if (!loading) {
    loading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = SCRIPT;
      s.async = true;
      s.onload = () => resolve();
      s.onerror = () => {
        loading = null;
        reject(new Error("주소 검색을 불러오지 못했습니다. 네트워크를 확인해 주세요."));
      };
      document.head.appendChild(s);
    });
  }
  return loading;
}

/** 주소 검색 창을 열고 → { address, juso: { siNm, sggNm } } (닫으면 null) */
export async function openPostcode() {
  await loadScript();
  return new Promise((resolve) => {
    let done = false;
    new window.daum.Postcode({
      oncomplete(data) {
        done = true;
        const road = data.roadAddress || data.address;
        const extra = data.buildingName && data.apartment === "Y" ? ` (${data.buildingName})` : "";
        resolve({
          address: `${road}${extra}`,
          juso: { siNm: SIDO_FULL[data.sido] ?? data.sido, sggNm: data.sigungu ?? "" },
        });
      },
      onclose() {
        if (!done) resolve(null);
      },
    }).open({ popupTitle: "본점 주소 검색" });
  });
}
