/**
 * IPO & New Listings Tracker Client Logic
 * Handles real-time search, categorization, pre-market synchronization,
 * and user tracking/notes for newly listed companies.
 */

class IPOTrackerApp {
  constructor() {
    this.currentFilter = 'all';
    this.searchQuery = '';
    this.currentSort = 'date_desc';
    this.activeListing = null; // currently selected for modal

    // DOM references
    this.cardsGrid = document.getElementById('ipoCardsGrid');
    this.loadingIndicator = document.getElementById('loadingIndicator');
    this.noResultsState = document.getElementById('noResultsState');
    this.noResultsText = document.getElementById('noResultsText');

    this.statTotal = document.getElementById('statTotalListings');
    this.statTracked = document.getElementById('statTrackedCount');
    this.stat2026 = document.getElementById('stat2026Count');
    this.statUpcoming = document.getElementById('statUpcomingCount');

    this.badgeAll = document.getElementById('badgeAll');
    this.badgeTracked = document.getElementById('badgeTracked');
    this.badge2026 = document.getElementById('badge2026');
    this.badgeMonth = document.getElementById('badgeMonth');
    this.badgeUpcoming = document.getElementById('badgeUpcoming');

    this.lastSyncLabel = document.getElementById('lastSyncLabel');
    this.btnSync = document.getElementById('btnSyncNewShares');
    this.searchInput = document.getElementById('ipoSearchInput');
    this.sortSelect = document.getElementById('ipoSortSelect');
    this.btnReset = document.getElementById('btnResetFilters');

    // Modal
    this.modal = document.getElementById('trackModal');
    this.modalTitle = document.getElementById('modalTitle');
    this.modalSymbol = document.getElementById('modalSymbol');
    this.modalName = document.getElementById('modalName');
    this.modalIcon = document.getElementById('modalIcon');
    this.modalTarget = document.getElementById('modalTargetPrice');
    this.modalNotes = document.getElementById('modalNotes');
    this.modalAlert = document.getElementById('modalAlert');
    this.modalSaveBtn = document.getElementById('modalSaveBtn');
    this.modalUntrackBtn = document.getElementById('modalUntrackBtn');
    this.modalCloseBtn = document.getElementById('modalCloseBtn');

    // Mobile nav
    this.mobileMenuBtn = document.getElementById('ipoMobileMenu');
    this.sidebar = document.getElementById('ipoNavigation');

    this.toast = document.getElementById('toast');
    this.toastTimeout = null;

    this.init();
  }

  init() {
    this.setupEvents();
    this.loadListings();
  }

  setupEvents() {
    // Mobile navigation toggle
    if (this.mobileMenuBtn && this.sidebar) {
      this.mobileMenuBtn.addEventListener('click', () => {
        const isOpen = this.sidebar.classList.toggle('results-nav-open');
        this.mobileMenuBtn.setAttribute('aria-expanded', String(isOpen));
      });
    }

    // Filter tabs
    const tabs = document.querySelectorAll('.filter-tab');
    tabs.forEach(tab => {
      tab.addEventListener('click', () => {
        tabs.forEach(t => {
          t.classList.remove('active');
          t.setAttribute('aria-selected', 'false');
        });
        tab.classList.add('active');
        tab.setAttribute('aria-selected', 'true');
        this.currentFilter = tab.getAttribute('data-filter') || 'all';
        this.loadListings();
      });
    });

    // Search with debounce
    let debounceTimer;
    this.searchInput.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        this.searchQuery = e.target.value.trim();
        this.loadListings();
      }, 250);
    });

    // Sort select
    this.sortSelect.addEventListener('change', (e) => {
      this.currentSort = e.target.value;
      this.loadListings();
    });

    // Reset filters
    this.btnReset.addEventListener('click', () => {
      this.searchInput.value = '';
      this.searchQuery = '';
      this.sortSelect.value = 'date_desc';
      this.currentSort = 'date_desc';
      this.loadListings();
    });

    // Sync button
    this.btnSync.addEventListener('click', () => this.syncNewShares());

    // Modal close
    this.modalCloseBtn.addEventListener('click', () => this.closeModal());
    this.modal.addEventListener('click', (e) => {
      if (e.target === this.modal) this.closeModal();
    });

    // Modal save
    this.modalSaveBtn.addEventListener('click', () => this.saveTracking());

    // Modal untrack
    this.modalUntrackBtn.addEventListener('click', () => this.untrackCompany());
  }

  async loadListings() {
    this.setLoading(true);

    try {
      const params = new URLSearchParams({
        filter: this.currentFilter,
        q: this.searchQuery,
        sort: this.currentSort,
        limit: '150'
      });

      const res = await fetch(`/api/ipo/listings?${params.toString()}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      this.renderListings(data);
      this.updateHeaderStats(data);
    } catch (err) {
      console.error('Failed to load IPO listings:', err);
      this.showToast('Could not fetch listings: ' + err.message, 'error');
      this.setLoading(false);
      this.noResultsState.hidden = false;
      this.noResultsText.textContent = 'Failed to load IPO data from server. Please retry.';
    }
  }

  renderListings(data) {
    this.setLoading(false);
    const items = data.items || [];

    if (items.length === 0) {
      this.cardsGrid.hidden = true;
      this.noResultsState.hidden = false;
      if (this.currentFilter === 'tracked') {
        this.noResultsText.textContent = "You aren't tracking any IPO companies yet. Click '☆ Track' on any company card to start following it.";
      } else {
        this.noResultsText.textContent = `No listings found matching "${this.searchQuery || this.currentFilter}".`;
      }
      return;
    }

    this.noResultsState.hidden = true;
    this.cardsGrid.hidden = false;
    this.cardsGrid.innerHTML = '';

    items.forEach(item => {
      const card = this.createCardElement(item);
      this.cardsGrid.appendChild(card);
    });
  }

  createCardElement(item) {
    const card = document.createElement('article');
    card.className = `ipo-card ${item.is_tracked ? 'is-tracked' : ''}`;
    card.id = `ipo-card-${item.symbol}`;

    // Badges & status styling
    let statusClass = 'listed';
    let statusLabel = 'LISTED';
    const isUpcoming = item.status === 'UPCOMING';
    const lDate = item.listing_date || 'TBD';

    if (isUpcoming) {
      statusClass = 'upcoming';
      statusLabel = 'UPCOMING IPO';
    } else if (lDate.includes('2026')) {
      statusClass = 'fresh';
      statusLabel = '2026 DEBUT';
    }

    // Price and Listing Gain
    let priceHtml = '';
    if (isUpcoming) {
      priceHtml = `
        <div class="ipo-metric-cell">
          <span class="ipo-metric-label">Issue Price</span>
          <span class="ipo-metric-val">₹${item.issue_price ? Number(item.issue_price).toLocaleString('en-IN') : 'TBD'}</span>
        </div>
        <div class="ipo-metric-cell">
          <span class="ipo-metric-label">Issue Size</span>
          <span class="ipo-metric-val">${item.issue_size_cr ? '₹' + Number(item.issue_size_cr).toLocaleString('en-IN') + ' Cr' : '—'}</span>
        </div>
      `;
    } else {
      const curPrice = item.current_price != null ? `₹${Number(item.current_price).toFixed(2)}` : 'Active on NSE';
      let gainBadge = '';
      if (item.listing_gain_pct != null) {
        const isPos = item.listing_gain_pct >= 0;
        gainBadge = `<span class="ipo-metric-val ${isPos ? 'gain-pos' : 'gain-neg'}">${isPos ? '+' : ''}${item.listing_gain_pct}%</span>`;
      } else if (item.issue_price) {
        gainBadge = `<span class="ipo-metric-val">Issue: ₹${item.issue_price}</span>`;
      } else {
        gainBadge = `<span class="ipo-metric-val" style="color:var(--muted)">Series ${item.series || 'EQ'}</span>`;
      }

      priceHtml = `
        <div class="ipo-metric-cell">
          <span class="ipo-metric-label">Current Price</span>
          <span class="ipo-metric-val" style="color:var(--blue)">${curPrice}</span>
        </div>
        <div class="ipo-metric-cell">
          <span class="ipo-metric-label">${item.listing_gain_pct != null ? 'Listing Gain' : 'Admission'}</span>
          ${gainBadge}
        </div>
      `;
    }

    // User notes if present
    let notesHtml = '';
    if (item.is_tracked && (item.user_notes || item.target_price)) {
      notesHtml = `
        <div class="ipo-notes-box">
          <strong>★ Tracked Notes:</strong>
          ${item.target_price ? `<div>Target Price: <b>₹${item.target_price}</b></div>` : ''}
          ${item.user_notes ? `<div>${this.escapeHtml(item.user_notes)}</div>` : ''}
        </div>
      `;
    }

    card.innerHTML = `
      <div class="ipo-card-header">
        <div class="ipo-symbol-group">
          <div class="ipo-icon">${item.symbol.substring(0, 3)}</div>
          <div class="ipo-title">
            <span class="ipo-symbol">${item.symbol}</span>
            <span class="ipo-name" title="${this.escapeHtml(item.name)}">${this.escapeHtml(item.name)}</span>
          </div>
        </div>
        <span class="status-badge ${statusClass}">${statusLabel}</span>
      </div>

      <div style="font-size:11px;color:var(--dim);display:flex;gap:12px;flex-wrap:wrap;">
        <span>Sector: <b style="color:var(--muted)">${item.sector || 'Equities'}</b></span>
        <span>Date: <b style="color:var(--text)">${lDate}</b></span>
        <span>Exch: <b style="color:var(--muted)">${item.exchange || 'NSE'}</b></span>
      </div>

      <div class="ipo-metrics-grid">
        ${priceHtml}
      </div>

      ${notesHtml}

      <div class="ipo-card-actions">
        <button type="button" class="track-btn ${item.is_tracked ? 'is-active' : ''}" data-symbol="${item.symbol}">
          ${item.is_tracked ? '★ Tracking' : '☆ Track'}
        </button>
        <a class="chart-link" href="/#scanner" title="Open ${item.symbol} in Workspace">
          View Chart ↗
        </a>
      </div>
    `;

    // Bind track button
    const trackBtn = card.querySelector('.track-btn');
    trackBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.openTrackModal(item);
    });

    return card;
  }

  updateHeaderStats(data) {
    if (this.statTotal) this.statTotal.textContent = data.total != null ? data.total : '—';
    if (this.statTracked) this.statTracked.textContent = data.tracked_count != null ? data.tracked_count : '0';
    if (data.last_sync && this.lastSyncLabel) {
      this.lastSyncLabel.textContent = `Last Synchronized: ${data.last_sync}`;
    }

    // Update filter badge counts
    if (this.badgeTracked && data.tracked_count != null) {
      this.badgeTracked.textContent = data.tracked_count;
    }
    if (this.badgeAll && data.total != null) {
      this.badgeAll.textContent = data.total;
    }

    // Estimate 2026 and upcoming count from fetched sample
    if (data.items) {
      const upCount = data.items.filter(i => i.status === 'UPCOMING').length;
      const count2026 = data.items.filter(i => (i.listing_date || '').includes('2026')).length;
      if (this.statUpcoming) this.statUpcoming.textContent = upCount;
      if (this.stat2026) this.stat2026.textContent = count2026;
      if (this.badgeUpcoming) this.badgeUpcoming.textContent = upCount;
      if (this.badge2026) this.badge2026.textContent = count2026;
    }
  }

  openTrackModal(item) {
    this.activeListing = item;
    this.modalSymbol.textContent = item.symbol;
    this.modalName.textContent = item.name;
    this.modalIcon.textContent = item.symbol.substring(0, 3);
    this.modalTarget.value = item.target_price || '';
    this.modalNotes.value = item.user_notes || '';
    this.modalAlert.checked = true;

    this.modalUntrackBtn.hidden = !item.is_tracked;
    this.modal.hidden = false;
    this.modalTarget.focus();
  }

  closeModal() {
    this.modal.hidden = true;
    this.activeListing = null;
  }

  async saveTracking() {
    if (!this.activeListing) return;

    const symbol = this.activeListing.symbol;
    const targetPrice = this.modalTarget.value ? parseFloat(this.modalTarget.value) : null;
    const notes = this.modalNotes.value.trim();
    const alertEnabled = this.modalAlert.checked;

    this.modalSaveBtn.disabled = true;
    this.modalSaveBtn.textContent = 'Saving…';

    try {
      const res = await fetch('/api/ipo/track', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          symbol: symbol,
          name: this.activeListing.name,
          listing_date: this.activeListing.listing_date,
          target_price: targetPrice,
          notes: notes,
          alert_enabled: alertEnabled
        })
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      this.showToast(`Tracking saved for ${symbol}!`, 'success');
      this.closeModal();
      this.loadListings();
    } catch (err) {
      console.error('Error saving tracking:', err);
      this.showToast('Failed to save tracking: ' + err.message, 'error');
    } finally {
      this.modalSaveBtn.disabled = false;
      this.modalSaveBtn.textContent = 'Save Tracking';
    }
  }

  async untrackCompany() {
    if (!this.activeListing) return;
    const symbol = this.activeListing.symbol;

    this.modalUntrackBtn.disabled = true;
    this.modalUntrackBtn.textContent = 'Removing…';

    try {
      const res = await fetch('/api/ipo/untrack', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: symbol })
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      this.showToast(`Removed ${symbol} from tracked IPOs`, 'success');
      this.closeModal();
      this.loadListings();
    } catch (err) {
      console.error('Error untracking company:', err);
      this.showToast('Failed to remove: ' + err.message, 'error');
    } finally {
      this.modalUntrackBtn.disabled = false;
      this.modalUntrackBtn.textContent = 'Untrack';
    }
  }

  async syncNewShares() {
    this.btnSync.disabled = true;
    const originalText = this.btnSync.innerHTML;
    this.btnSync.innerHTML = '<span class="loading-ring" style="width:14px;height:14px;border-width:2px;margin:0;" aria-hidden="true"></span> Syncing exchange shares…';

    try {
      const res = await fetch('/api/ipo/sync', { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const result = await res.json();

      const added = result.newly_added_count || 0;
      const total = result.total_universe_stocks || 2613;

      this.showToast(`⚡ Pre-market sync complete: ${added} new shares ingested. Active universe: ${total} stocks!`, 'success');
      if (this.lastSyncLabel) {
        this.lastSyncLabel.textContent = `Last Synchronized: ${result.sync_time || 'Just now'}`;
      }
      const heroUniverse = document.getElementById('heroTotalUniverse');
      if (heroUniverse) heroUniverse.textContent = `${total.toLocaleString('en-IN')}+ Equities`;

      this.loadListings();
    } catch (err) {
      console.error('Pre-market sync failed:', err);
      this.showToast('Pre-market sync failed: ' + err.message, 'error');
    } finally {
      this.btnSync.disabled = false;
      this.btnSync.innerHTML = originalText;
    }
  }

  setLoading(isLoading) {
    if (this.loadingIndicator) this.loadingIndicator.hidden = !isLoading;
    if (isLoading) {
      this.cardsGrid.hidden = true;
      this.noResultsState.hidden = true;
    }
  }

  showToast(message, type = 'success') {
    if (!this.toast) return;
    clearTimeout(this.toastTimeout);
    this.toast.textContent = message;
    this.toast.style.borderColor = type === 'error' ? 'var(--red)' : 'var(--green)';
    this.toast.hidden = false;

    this.toastTimeout = setTimeout(() => {
      this.toast.hidden = true;
    }, 4000);
  }

  escapeHtml(str) {
    if (!str) return '';
    return str.replace(/[&<>'"]/g, tag => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      "'": '&#39;',
      '"': '&quot;'
    }[tag] || tag));
  }
}

// Instantiate on DOM load
document.addEventListener('DOMContentLoaded', () => {
  new IPOTrackerApp();
});
