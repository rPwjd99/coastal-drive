<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8" />
  <title>바다따라: 해안도로 감성 드라이브</title>
  <script src="https://cdn.jsdelivr.net/npm/ol@7.3.0/dist/ol.js"></script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ol@7.3.0/ol.css" />
  <style>
    #map {
      width: 100%;
      height: 90vh;
    }
    body {
      font-family: sans-serif;
      margin: 0;
    }
    #form {
      padding: 1rem;
      background: #f0f0f0;
    }
    input {
      padding: 0.5rem;
      margin-right: 0.5rem;
      width: 300px;
    }
    button {
      padding: 0.5rem 1rem;
    }
  </style>
</head>
<body>
  <div id="form">
    <input id="start" placeholder="출발지 주소" value="세종특별자치시 한누리대로 2130" />
    <input id="end" placeholder="목적지 주소" value="강원도 소초시 중앙로 183" />
    <button onclick="searchRoute()">해안도로 경로 검색</button>
  </div>
  <div id="map"></div>

  <script>
    const map = new ol.Map({
      target: 'map',
      layers: [
        new ol.layer.Tile({ source: new ol.source.OSM() })
      ],
      view: new ol.View({
        center: ol.proj.fromLonLat([127.5, 36.5]),
        zoom: 7
      })
    });

    let routeLayer = null;

    function searchRoute() {
      const start = document.getElementById("start").value;
      const end = document.getElementById("end").value;

      fetch("/route", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start, end })
      })
      .then(res => {
        if (!res.ok) throw new Error("❌ 서버 연락 실패");
        return res.json();
      })
      .then(data => {
        if (!data.route || !data.route.traoptimal) throw new Error("❌ 경로 정보 없음");

        const path = data.route.traoptimal[0].path.map(([x, y]) => ol.proj.fromLonLat([x, y]));
        const feature = new ol.Feature({ geometry: new ol.geom.LineString(path) });

        if (routeLayer) map.removeLayer(routeLayer);
        routeLayer = new ol.layer.Vector({
          source: new ol.source.Vector({ features: [feature] }),
          style: new ol.style.Style({
            stroke: new ol.style.Stroke({ color: '#007bff', width: 4 })
          })
        });
        map.addLayer(routeLayer);
      })
      .catch(err => {
        console.error("❌ 예외 발생:", err);
        alert(err.message);
      });
    }
  </script>
</body>
</html>
