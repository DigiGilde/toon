// The deck's own script, written the way single-page slide decks commonly
// are: a classic script at the end of the body that reads the slide from
// `#/N` once, rewrites the address on every change and takes the keys on the
// whole document. It has no hashchange listener.
(function () {
  var slides = Array.prototype.slice.call(document.querySelectorAll('.slide'));
  var counter = document.getElementById('counter');
  var scrolling = window.matchMedia('(max-width: 900px), (max-height: 500px)');

  function fromHash() {
    var match = /^#\/(\d+)/.exec(location.hash);
    return match ? Number(match[1]) - 1 : -1;
  }

  var index = slides[fromHash()] ? fromHash() : 0;

  function fit() {
    var scale = Math.min(window.innerWidth / 1000, window.innerHeight / 580);
    document.documentElement.style.setProperty('--scale', String(scale));
  }

  function render() {
    fit();
    if (scrolling.matches) {
      slides.forEach(function (slide) {
        slide.hidden = false;
      });
      return;
    }
    slides.forEach(function (slide, k) {
      slide.hidden = k !== index;
    });
    counter.textContent = index + 1 + ' / ' + slides.length;
    history.replaceState(null, '', location.pathname + '#/' + (index + 1));
  }

  function go(to) {
    if (!slides[to]) return;
    index = to;
    render();
  }

  document.addEventListener('keydown', function (event) {
    if (scrolling.matches || event.metaKey || event.ctrlKey || event.altKey) return;
    if ((event.key === ' ' || event.key === 'Enter') && event.target.closest && event.target.closest('a, button')) {
      return;
    }
    switch (event.key) {
      case ' ':
      case 'ArrowRight':
      case 'PageDown':
      case 'n':
        event.preventDefault();
        go(index + 1);
        break;
      case 'ArrowLeft':
      case 'PageUp':
      case 'p':
        event.preventDefault();
        go(index - 1);
        break;
      case 'Home':
        event.preventDefault();
        go(0);
        break;
      case 'End':
        event.preventDefault();
        go(slides.length - 1);
        break;
    }
  });

  scrolling.addEventListener('change', render);
  window.addEventListener('resize', fit);
  render();
})();
