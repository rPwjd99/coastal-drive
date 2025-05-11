# 바다따라 🌊  
해안도로 감성 드라이브 코스 추천 웹앱

## 📌 개요  
사용자가 출발지와 도착지 주소를 입력하면,  
가장 가까운 해안선을 경유하여 바다를 더 많이 볼 수 있는  
해안도로 중심의 드라이브 경로를 추천하는 웹 서비스입니다.

## 💡 주요 기능  
✅ 출발지 → 해안 경유 → 목적지 경로 자동 생성  
✅ 해안선 GeoJSON 기반 우회 포인트 자동 탐색  
✅ Naver Directions API로 실제 도로 기반 경로 계산  
✅ Google Maps Geocoding API로 정확한 주소 → 좌표 변환  
✅ 한국관광공사 TourAPI로 목적지 주변 관광지 자동 표시 (마커 및 팝업)  
✅ Render를 통한 Flask 기반 웹앱 배포

## 🗺️ 데모  
👉 [바다따라: 해안도로 드라이브 경로 추천](https://coastal-drive.onrender.com)

## 🧩 사용 기술  
- **Naver Directions API** – 자동차 경로 계산  
- **Google Maps Geocoding API** – 주소를 위경도로 변환  
- **한국관광공사 TourAPI** – 주변 관광지 검색  
- **GeoPandas, Shapely** – 해안선 분석 및 거리 계산  
- **OpenLayers** – 지도 시각화 (경로 및 마커 표시)  
- **Flask, Python** – 백엔드 서버 및 API 처리  
- **Render** – 전체 서비스 배포

## 🔧 실행 흐름  
1. 사용자가 출발지와 목적지 주소를 입력  
2. 주소를 위도·경도로 변환 (Google Maps API 사용)  
3. 출발지 또는 목적지 기준으로 가장 가까운 해안선 좌표(wpt) 자동 탐색  
4. `출발지 → 해안 → 목적지` 순서로 도로 경로 계산 (Naver Directions API)  
5. 목적지 주변 관광지를 반경 5km 내에서 검색 (TourAPI)  
6. 지도(OpenLayers)에 전체 경로와 관광지 마커 표시

## 📍 특이사항 및 설계 기준  
- 해안선은 국가기본도 기반 `coastal_route_result.geojson`을 사용  
- waypoint는 반드시 해안선 상에 존재하며, 도로 연결을 위해 반경 10km 이내 연결 가능 지점 자동 탐색  
- 해안선 위도 또는 경도 중 목적지 방향과 유사한 경로를 선별하여 해안도로 중심 경유 루트를 설계

## 🔗 데이터 출처  
- 해안선: [국가기본도 GeoJSON (EPSG:4326)]  
- 도로 경로: [Naver Cloud Platform Directions API]  
- 주소 변환: [Google Maps Geocoding API]  
- 관광지: [한국관광공사 TourAPI]

## 👨‍💻 개발자  
- GitHub: [rPwjd99](https://github.com/rPwjd99)  
- 주제: 2025 관광데이터 활용 공모전
