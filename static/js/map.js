
    const frame = document.getElementById("myMapsFrame");
    const empty = document.getElementById("emptyMap");
    const mapContainer = document.getElementById("mapContainer");
    if (!frame.getAttribute("src")) {
      mapContainer.hidden = true;
      empty.hidden = false;
    }
  