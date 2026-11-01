// One line-icon style for the whole UI: 24px grid, 1.75 stroke, round caps/joins,
// currentColor. Drawn inline (lucide-like line art), no icon dependency.
const P = {
  book:      '<path d="M4 4.5A1.5 1.5 0 0 1 5.5 3H20v15H5.5A1.5 1.5 0 0 0 4 19.5z"/><path d="M4 19.5A1.5 1.5 0 0 0 5.5 21H20v-3"/><path d="M8 7h8M8 10.5h6"/>',
  map:       '<path d="M9 4 3 6.5v13.5l6-2.5 6 2.5 6-2.5V4l-6 2.5z"/><path d="M9 4v13.5M15 6.5V20"/>',
  rewind:    '<path d="M11 18 3 12l8-6z"/><path d="M21 18l-8-6 8-6z"/>',
  web:       '<circle cx="6" cy="6" r="2.2"/><circle cx="18" cy="7" r="2.2"/><circle cx="12" cy="18" r="2.2"/><path d="M8 6.5l7.8.4M7 8l4 8M17 9l-4 7"/>',
  play:      '<path d="M7 4.5v15l12-7.5z"/>',
  pause:     '<path d="M8 5v14M16 5v14"/>',
  left:      '<path d="M15 5l-7 7 7 7"/>',
  right:     '<path d="M9 5l7 7-7 7"/>',
  down:      '<path d="M12 5v14M6 13l6 6 6-6"/>',
  x:         '<path d="M6 6l12 12M18 6 6 18"/>',
  lock:      '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
  wrench:    '<path d="M14.5 6.5a4 4 0 0 0 5 5l-8.5 8.5a2.1 2.1 0 0 1-3-3L16.5 8.5a4 4 0 0 0-2-2z"/>',
  sun:       '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
  sunset:    '<path d="M3 18h18M7 18a5 5 0 0 1 10 0"/><path d="M12 4v4M5 9.5l1.5 1.5M19 9.5 17.5 11M3 21.5h18"/>',
  moon:      '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
  say:       '<path d="M4 5h16v11H9l-5 4z"/>',
  sprout:    '<path d="M12 21v-9"/><path d="M12 12c0-4 3-6 7-6 0 4-3 6-7 6zM12 14c0-3-2.5-5-6-5 0 3 2.5 5 6 5z"/>',
  flag:      '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
  scale:     '<path d="M12 4v16M7 20h10M5 7h14"/><path d="M5 7l-2.5 6a2.5 2.5 0 0 0 5 0zM19 7l-2.5 6a2.5 2.5 0 0 0 5 0z"/>',
  bookopen:  '<path d="M3 5.5h6a3 3 0 0 1 3 3V20a2.5 2.5 0 0 0-2.5-2.5H3zM21 5.5h-6a3 3 0 0 0-3 3V20a2.5 2.5 0 0 1 2.5-2.5H21z"/>',
  bookmark:  '<path d="M6 3h12v18l-6-4-6 4z"/>',
  heart:     '<path d="M12 20s-7.5-4.5-7.5-10A4.2 4.2 0 0 1 12 7.5 4.2 4.2 0 0 1 19.5 10c0 5.5-7.5 10-7.5 10z"/>',
  heartoff:  '<path d="M12 20s-7.5-4.5-7.5-10A4.2 4.2 0 0 1 12 7.5 4.2 4.2 0 0 1 19.5 10c0 5.5-7.5 10-7.5 10z"/><path d="M12 7.5 10.5 11l3 2-1.5 3.5"/>',
  ring:      '<circle cx="12" cy="14.5" r="5.5"/><path d="M9.5 5.5 12 3l2.5 2.5L12 9z"/>',
  users:     '<circle cx="9" cy="8" r="3.2"/><path d="M3 20a6 6 0 0 1 12 0"/><path d="M16 4.8a3.2 3.2 0 0 1 0 6.4M17.5 14.5A6 6 0 0 1 21 20"/>',
  whisper:   '<path d="M4 5h16v11H9l-5 4z"/><path d="M9 10.5h6"/>',
  megaphone: '<path d="M4 10v4h3l8 4V6l-8 4z"/><path d="M18 9.5a3.5 3.5 0 0 1 0 5M7 14l1 5h2.5"/>',
  palette:   '<path d="M12 3a9 9 0 1 0 0 18c1.2 0 1.6-1 1.2-2-.6-1.5.4-2.5 1.8-2.5H18a3 3 0 0 0 3-3A9 9 0 0 0 12 3z"/><circle cx="8" cy="10" r="1"/><circle cx="12" cy="7.5" r="1"/><circle cx="16" cy="10" r="1"/>',
  shuffle:   '<path d="M3 7h4l10 10h4M3 17h4l3-3M14 10l3-3h4M18 4l3 3-3 3M18 14l3 3-3 3"/>',
  rain:      '<path d="M7 15a4.5 4.5 0 1 1 1-8.9A5.5 5.5 0 0 1 18.5 8 3.5 3.5 0 0 1 18 15z"/><path d="M8 18l-1 2.5M12 18l-1 2.5M16 18l-1 2.5"/>',
  coins:     '<ellipse cx="10" cy="7" rx="6" ry="2.5"/><path d="M4 7v4c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5V7"/><path d="M8 15.5c.6 1.2 3 2 6 2 3.3 0 6-1.1 6-2.5v-4c0-.9-1-1.7-2.7-2.1"/>',
  bulb:      '<path d="M9 18h6M10 21h4"/><path d="M8.5 14.5A6 6 0 1 1 15.5 14.5c-.8.7-1.5 1.6-1.5 2.5h-4c0-.9-.7-1.8-1.5-2.5z"/>',
  dot:       '<circle cx="12" cy="12" r="2.5"/>',
  star:      '<path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.8 6.8 19.6l1-5.8-4.3-4.1 5.9-.8z"/>',
  chart:     '<path d="M4 4v16h16"/><path d="M7 15l4-5 3 3 5-6"/>',
};

export const Icon = {
  props: {name: String, size: {type: Number, default: 16}},
  computed: { svg() { return P[this.name] || P.dot; } },
  template: `<svg class="icon" :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" v-html="svg"></svg>`,
};

// Event verb -> icon name (one icon language across column, chronicle, replay, cards).
const VERB_ICON = {
  say: 'say', insight: 'bulb', belief: 'bulb', share_rumor: 'megaphone', leak: 'megaphone', confide: 'whisper',
  wish_born: 'sprout', wish_seeded: 'sprout', wish_closed: 'flag', wish_frustrated: 'flag',
  chapter_closed: 'bookopen', chapter_started: 'bookmark', confronted: 'scale',
  romance_dating: 'heart', romance_rejected: 'heartoff', romance_partners: 'ring',
  milestone: 'users', talk_start: 'users', met_up: 'users', meetup_arranged: 'users', meetup_declined: 'users',
  landmark_done: 'palette', transition: 'shuffle', rain_start: 'rain', rain_end: 'sun', festival_start: 'star', festival_end: 'star',
  breakdown: 'wrench', repaired: 'wrench', day_summary: 'coins', broke: 'coins', secret_born: 'lock', week_close: 'coins',
};
export const verbIcon = v => VERB_ICON[v] || 'dot';
