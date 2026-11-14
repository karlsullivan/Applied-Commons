(function () {
  var box = document.getElementById("q"), out = document.getElementById("results");
  var params = new URLSearchParams(location.search);
  function words(s) { return s.toLowerCase().split(/\s+/).filter(Boolean); }
  function run() {
    var q = words(box.value); out.innerHTML = "";
    if (!q.length) return;
    var hits = (window.AC_INDEX || []).map(function (d) {
      var hay = (d.t + " " + d.s + " " + d.k).toLowerCase(), score = 0;
      for (var i = 0; i < q.length; i++) {
        if (hay.indexOf(q[i]) < 0) return null;
        score += d.t.toLowerCase().indexOf(q[i]) >= 0 ? 3 : 1;
      }
      return [score, d];
    }).filter(Boolean).sort(function (a, b) { return b[0] - a[0]; }).slice(0, 100);
    var n = document.createElement("p"); n.className = "muted";
    n.textContent = hits.length + (hits.length === 1 ? " result" : " results"); out.appendChild(n);
    hits.forEach(function (h) {
      var li = document.createElement("li"), a = document.createElement("a");
      a.href = h[1].u; a.textContent = h[1].t; li.appendChild(a);
      var k = document.createElement("span"); k.className = "muted"; k.textContent = " (" + h[1].k + ")";
      li.appendChild(k);
      var s = document.createElement("div"); s.className = "snippet"; s.textContent = h[1].s;
      li.appendChild(s); out.appendChild(li);
    });
  }
  box.value = params.get("q") || ""; box.addEventListener("input", run); run();
})();
