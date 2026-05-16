// Set up the Leaflet GeoSearch control as a standalone search bar
const provider = new window.GeoSearch.OpenStreetMapProvider({
    params: {
        countrycodes: 'us',
        addressdetails: 1,
        limit: 8,
    },
});

const searchContainer = document.getElementById('location-search');
const displayInput = document.getElementById('location-display');
const latInput = document.getElementById('location-lat');
const lonInput = document.getElementById('location-lon');
const resolvedHint = document.getElementById('location-resolved');

// Build a minimal text input + dropdown manually (we're not using a map yet)
const searchInput = document.createElement('input');
searchInput.type = 'text';
searchInput.placeholder = 'e.g., Bogue Chitto, Mississippi';
searchInput.required = true;
searchInput.autocomplete = 'off';
searchInput.className = 'w-full border-2 border-slate-300 px-3 py-2 focus:border-[#003a8c] focus:outline-none';
searchContainer.appendChild(searchInput);

const dropdown = document.createElement('div');
dropdown.className = 'absolute left-0 right-0 top-full mt-1 bg-white border-2 border-[#003a8c] shadow-lg z-50 hidden max-h-64 overflow-y-auto';
searchContainer.appendChild(dropdown);

let debounceTimer = null;
let lastQuery = '';

searchInput.addEventListener('input', (e) => {
    const query = e.target.value.trim();
    clearTimeout(debounceTimer);

    latInput.value = '';
    lonInput.value = '';
    displayInput.value = '';
    resolvedHint.classList.add('hidden');

    if (query.length < 3 || query === lastQuery) {
        dropdown.classList.add('hidden');
        return;
    }
    lastQuery = query;

    debounceTimer = setTimeout(async () => {
        try {
            const results = await provider.search({ query });
            renderDropdown(results);
        } catch (err) {
            console.error('Search failed', err);
        }
    }, 300);
});

function renderDropdown(results) {
    dropdown.innerHTML = '';
    if (!results || results.length === 0) {
        const empty = document.createElement('div');
        empty.className = 'px-3 py-2 text-sm text-slate-500 italic';
        empty.textContent = 'No matches.';
        dropdown.appendChild(empty);
        dropdown.classList.remove('hidden');
        return;
    }

    results.forEach((r) => {
        const item = document.createElement('button');
        item.type = 'button';
        item.className = 'w-full text-left px-3 py-2 text-sm hover:bg-yellow-100 border-b border-slate-200 last:border-b-0';
        item.textContent = r.label;
        item.addEventListener('click', () => selectLocation(r));
        dropdown.appendChild(item);
    });
    dropdown.classList.remove('hidden');
}

function selectLocation(result) {
    searchInput.value = result.label;
    displayInput.value = result.label;
    latInput.value = result.y;
    lonInput.value = result.x;
    resolvedHint.textContent = `✓ Selected: ${result.label}`;
    resolvedHint.classList.remove('hidden');
    dropdown.classList.add('hidden');
}

document.addEventListener('click', (e) => {
    if (!searchContainer.contains(e.target)) {
        dropdown.classList.add('hidden');
    }
});

// Form submission
const form = document.getElementById('report-form');
const statusDiv = document.getElementById('status');
const statusText = statusDiv.querySelector('p');
const submitBtn = document.getElementById('submit-btn');

// Update time labels based on tz mode
document.querySelectorAll('input[name="tz_mode"]').forEach(radio => {
    radio.addEventListener('change', (e) => {
        const label = e.target.value === 'utc' ? '(UTC)' : '(local)';
        document.getElementById('start-tz-label').textContent = label;
        document.getElementById('end-tz-label').textContent = label;
    });
});

form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(form));

    submitBtn.disabled = true;
    submitBtn.textContent = '⏳ Generating...';
    statusDiv.classList.remove('hidden');
    statusText.textContent = '▶ Submitting request and running pipeline. This may take 1-3 minutes...';

    try {
        const res = await fetch('/reports', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
            redirect: 'follow',
        });
        if (res.redirected) {
            window.location.href = res.url;
        } else if (res.ok) {
            statusText.textContent = 'Report generated.';
        } else {
            const err = await res.json();
            statusText.textContent = 'Error: ' + (err.detail || 'unknown');
            submitBtn.disabled = false;
            submitBtn.textContent = 'Generate Report';
        }
    } catch (err) {
        statusText.textContent = 'Network error: ' + err.message;
        submitBtn.disabled = false;
        submitBtn.textContent = 'Generate Report';
    }
});