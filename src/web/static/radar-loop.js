(function () {
    const img = document.getElementById("loop-img");
    if (!img) return;

    const frames = JSON.parse(img.dataset.frames);
    const timestamps = JSON.parse(img.dataset.timestamps);
    const frameLabel = document.getElementById("loop-frame-label");
    const timestampLabel = document.getElementById("loop-timestamp");
    const scrubber = document.getElementById("loop-scrubber");
    const playPause = document.getElementById("loop-playpause");
    const speedSelect = document.getElementById("loop-speed");
    const expandBtn = document.getElementById("loop-expand");

    let currentFrame = 0;
    let playing = true;
    let intervalMs = parseInt(speedSelect.value, 10);
    let timerId = null;

    // Preload frames so animation doesn't stutter
    frames.forEach((src) => {
        const preload = new Image();
        preload.src = src;
    });

    function pad(n) {
        return String(n).padStart(2, "0");
    }

    function showFrame(idx) {
        currentFrame = idx;
        img.src = frames[idx];
        scrubber.value = idx;
        frameLabel.textContent = `FRAME ${pad(idx + 1)} OF ${pad(frames.length)}`;
        if (timestamps[idx]) {
            timestampLabel.textContent = timestamps[idx].substring(0, 19);
        }
    }

    function advance() {
        showFrame((currentFrame + 1) % frames.length);
    }

    function startLoop() {
        if (timerId) clearInterval(timerId);
        timerId = setInterval(advance, intervalMs);
        playing = true;
        playPause.textContent = "⏸ PAUSE";
    }

    function stopLoop() {
        if (timerId) clearInterval(timerId);
        timerId = null;
        playing = false;
        playPause.textContent = "▶ PLAY";
    }

    playPause.addEventListener("click", () => {
        if (playing) stopLoop();
        else startLoop();
    });

    scrubber.addEventListener("input", (e) => {
        stopLoop();
        showFrame(parseInt(e.target.value, 10));
    });

    speedSelect.addEventListener("change", (e) => {
        intervalMs = parseInt(e.target.value, 10);
        if (playing) startLoop();
    });

    // Click image → expand to full gallery in lightbox
    expandBtn.addEventListener("click", (e) => {
        e.preventDefault();
        stopLoop();
        // Open lightbox with current frame; lightbox.js handles the rest
        if (window.openLightboxGallery) {
            window.openLightboxGallery(frames, timestamps, currentFrame);
        }
    });

    // Auto-start
    startLoop();

    // Pause when tab is hidden (saves CPU)
    document.addEventListener("visibilitychange", () => {
        if (document.hidden && playing) stopLoop();
    });
})();