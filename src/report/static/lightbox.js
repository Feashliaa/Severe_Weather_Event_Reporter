(function () {
    const lightbox = document.getElementById("lightbox");
    const lightboxImg = document.getElementById("lightbox-img");
    const lightboxCaption = document.getElementById("lightbox-caption");
    const closeBtn = document.getElementById("lightbox-close");

    if (!lightbox) return;

    let gallery = null;
    let galleryIdx = 0;

    function show(src, caption) {
        lightboxImg.src = src;
        lightboxImg.alt = caption || "";
        lightboxCaption.textContent = caption || "";
    }

    function open(src, caption) {
        gallery = null;
        show(src, caption);
        lightbox.classList.remove("hidden");
        lightbox.classList.add("flex");
        document.body.style.overflow = "hidden";
        updateNavVisibility();
    }

    function openGallery(frames, timestamps, startIdx) {
        gallery = { frames, timestamps };
        galleryIdx = startIdx || 0;
        showGalleryFrame();
        lightbox.classList.remove("hidden");
        lightbox.classList.add("flex");
        document.body.style.overflow = "hidden";
        updateNavVisibility();
    }

    function showGalleryFrame() {
        if (!gallery) return;
        const ts = gallery.timestamps[galleryIdx] || "";
        show(
            gallery.frames[galleryIdx],
            `${ts.substring(0, 19)} UTC — Frame ${galleryIdx + 1} of ${gallery.frames.length}`
        );
    }

    function next() {
        if (!gallery) return;
        galleryIdx = (galleryIdx + 1) % gallery.frames.length;
        showGalleryFrame();
    }

    function prev() {
        if (!gallery) return;
        galleryIdx = (galleryIdx - 1 + gallery.frames.length) % gallery.frames.length;
        showGalleryFrame();
    }

    function close() {
        lightbox.classList.add("hidden");
        lightbox.classList.remove("flex");
        lightboxImg.src = "";
        document.body.style.overflow = "";
        gallery = null;
    }

    function updateNavVisibility() {
        const navPrev = document.getElementById("lightbox-prev");
        const navNext = document.getElementById("lightbox-next");
        if (navPrev && navNext) {
            const show = gallery ? "flex" : "none";
            navPrev.style.display = show;
            navNext.style.display = show;
        }
    }

    document.querySelectorAll("[data-lightbox-src]").forEach((el) => {
        el.addEventListener("click", () => {
            open(el.dataset.lightboxSrc, el.dataset.lightboxCaption);
        });
    });

    closeBtn.addEventListener("click", close);
    lightbox.addEventListener("click", (e) => { if (e.target === lightbox) close(); });

    document.addEventListener("keydown", (e) => {
        if (lightbox.classList.contains("hidden")) return;
        if (e.key === "Escape") close();
        else if (e.key === "ArrowRight") next();
        else if (e.key === "ArrowLeft") prev();
    });

    // Wire up nav buttons if they exist
    const navPrev = document.getElementById("lightbox-prev");
    const navNext = document.getElementById("lightbox-next");
    if (navPrev) navPrev.addEventListener("click", prev);
    if (navNext) navNext.addEventListener("click", next);

    // Expose for other scripts
    window.openLightboxGallery = openGallery;
})();