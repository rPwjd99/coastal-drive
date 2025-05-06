# 바다따라 🌊  
해안도로 감성 드라이브 코스 추천 웹앱

---

## 📌 개요

사용자가 출발지와 도착지 주소를 입력하면,  
가장 가까운 해안선을 찾아 우회 경로를 설정하고  
해안도로 중심의 드라이브 루트를 시각화하는 웹 서비스입니다.

---

## 💡 주요 기능

- ✅ 출발지 → 해안 경유 → 목적지 경로 자동 생성
- ✅ 해안선 GeoJSON 기반 우회 포인트 자동 탐색
- ✅ OpenRouteService API로 실제 도로 경로 계산
- ✅ 주변 관광지(TourAPI) 자동 표시 (마커 및 팝업)
- ✅ GitHub Pages를 통한 웹 배포

---

## 🗺️ 데모

[👉 웹에서 바로 보기](https://rpwjd99.github.io/coastal-drive/index_with_tour.html)

---

## 🧩 사용 기술

- **OpenRouteService API** – 자동차 경로 계산
- **VWorld Geocoder API** – 주소 → 좌표 변환
- **한국관광공사 TourAPI** – 주변 관광지 정보 연동
- **GeoPandas, Shapely, Requests** – Python 공간 분석
- **OpenLayers** – HTML 지도 시각화

---

## 🔧 실행 흐름

1. 사용자가 주소를 입력하면 좌표로 변환
2. 출발지 기준 가장 가까운 해안선을 탐색
3. 우회 좌표를 거쳐 목적지까지 경로 계산
4. 지도에 전체 경로와 관광지 팝업 마커 표시

---

## 🔗 데이터 출처

- 해안선: 국가기본도 GeoJSON (EPSG:4326)
- 도로경로: OpenRouteService
- 관광지: 한국관광공사 TourAPI

---

## 👨‍💻 개발자

- GitHub: [rPwjd99](https://github.com/rPwjd99)
