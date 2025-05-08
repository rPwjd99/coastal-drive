<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>해안도로 감성 드라이브</title>
  <script src="https://cdn.jsdelivr.net/npm/ol@7.3.0/dist/ol.js"></script>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/ol@7.3.0/ol.css">
  <style>
    body { font-family: sans-serif; margin: 0; padding: 0; }
    #map { width: 100%; height: 80vh; }
    .controls { padding: 1rem; background: #f0f0f0; }
    .controls input { width: 300px; margin-right: 0.5rem; }
  </style>
</head>
<body>
  <div class="controls">
    <h2>해안도로 감성 드라이브 경로 검색</h2>
    <div>
      출발지: <input id="start" placeholder="예: 세종시청">
      <button onclick="search('start')">주소 검색</button>
      <span id="startResult"></span>
    </div>
    <div style="margin-top: 0.5rem;">
      목적지: <input id="end" placeholder="예: 속초시청">
      <button onclick="search('end')">주소 검색</button>
      <span id="endResult"></span>
    </div>
    <div style="margin-top: 1rem;">
      <button onclick="searchRoute()">해안도로 경로 검색</button>
    </div>
  </div>
  <div id="map"></div>

  <script>
    let map = new ol.Map({
      target: 'map',
      layers: [new ol.layer.Tile({ source: new ol.source.OSM() })],
      view: new ol.View({ center: ol.proj.fromLonLat([127.5, 36.5]), zoom: 8 })
    });

    let startCoord = null;
    let endCoord = null;

    function search(type) {
      const input = document.getElementById(type).value;
      fetch('/geocode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ address: input })
      })
      .then(res => res.json())
      .then(data => {
        if (data.lat && data.lng) {
          if (type === 'start') startCoord = [data.lng, data.lat];
          else endCoord = [data.lng, data.lat];
          document.getElementById(type + 'Result').textContent = `✅ 위치: ${data.lat}, ${data.lng}`;
        } else {
          document.getElementById(type + 'Result').textContent = '❌ 검색 실패';
        }
      });
    }

    function searchRoute() {
      if (!startCoord || !endCoord) return alert('출발지와 도착지를 모두 검색해 주세요');
      fetch('/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ start: startCoord, end: endCoord })
      })
      .then(res => res.json())
      .then(geojson => drawRoute(geojson))
      .catch(err => alert('❌ 경로 요청 실패'));
    }

    function drawRoute(geojson) {
      const format = new ol.format.GeoJSON();
      const features = format.readFeatures(geojson, {
        featureProjection: 'EPSG:3857'
      });

      const layer = new ol.layer.Vector({
        source: new ol.source.Vector({ features }),
        style: new ol.style.Style({
          stroke: new ol.style.Stroke({ color: 'blue', width: 4 })
        })
      });

      map.getLayers().getArray().slice(1).forEach(l => map.removeLayer(l));
      map.addLayer(layer);

      const extent = layer.getSource().getExtent();
      map.getView().fit(extent, { padding: [30, 30, 30, 30] });
    }
  </script>
</body>
</html>
