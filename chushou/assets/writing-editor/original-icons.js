// Glyph identities from the original Shensi whiteboard and directory menus.
const codes={move:0xE8DE,share:0xE72D,upload:0xE898,'reference-canvas':0xE8F1,sort:0xE8CB,restore:0xE777,novel:0xE82D,document:0xE8A5,generate:0xE945,preview:0xE740,versions:0xE81C,history:0xE81C,'save-history':0xE74E,attachment:0xE91B,edit:0xE70F,'smart-split':0xE8C6,'create-depth':0xE945,'generate-text':0xE8D2,'generate-image':0xE91B,'generate-video':0xE714,'generate-audio':0xE8D6,'add-here':0xE710,'add-plain':0xE710,'reference-web':0xE774,'reference-document':0xE8A5,'reference-skill':0xE943,'reference-book':0xE82D,reference:0xE71B,link:0xE71B,'save-as-note':0xE8A5,'copy-cards':0xE8C8,'cut-cards':0xE8C6,'paste-cards':0xE77F,'duplicate-card':0xE7C5,'save-as':0xE792,reveal:0xE8B7,'link-upstream':0xE71B,focus:0xE81E,arrange:0xE8DE,'arrange-all':0xE8DE,color:0xE790,'delete-cards':0xE74D,'delete-edge':0xE74D,'select-cards':0xE8B3,fit:0xE81E,generation:0xE945,'edit-image':0xE70F,separate:0xE8C6,trim:0xE8C6,frame:0xE91B,copy:0xE8C8,cut:0xE8C6,paste:0xE77F,duplicate:0xE7C5,rename:0xE70F,delete:0xE74D,remove:0xE74D,export:0xE792,'new-note':0xE8A5,note:0xE8A5,'new-folder':0xE8B7,folder:0xE8B7,'new-board':0xE7C3,board:0xE7C3};
codes['parse-video']=0xE774;
codes.separate=0xE8D6;
codes['save-as']=0xE896;
codes.frame=0xE722;
// Most actions use scalable round-ended strokes. Focus and preview controls
// retain the original Shensi font glyphs requested by the user.
const shapes={
 move:'M12 3v18M3 12h18m-13-5 4-4 4 4m-8 10 4 4 4-4M7 8l-4 4 4 4m10-8 4 4-4 4',
 share:'M12 16V3m-4 4 4-4 4 4M5 12v7a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-7',
 document:'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5v5h5M8 12h8M8 16h6',
 folder:'M3 8V6a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8Z',
 board:'M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Zm3 18 4-4 4 4M7 8h4v5H7m8-5h2m-2 4h2',
 image:'M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Zm-2 14 6-6 4 4 3-3 5 5M16 7h.01',
 video:'M5 5h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Zm12 5 4-3v10l-4-3',
 audio:'M9 18V5l11-2v13M9 8l11-2M9 18c0 4-7 4-7 0s7-4 7 0Zm11-2c0 4-7 4-7 0s7-4 7 0Z',
 edit:'m16 4 4 4M4 20l5-1L20 8a2.8 2.8 0 0 0-4-4L5 15z',
 history:'M3 10a9 9 0 1 1 2.6 8M3 4v6h6M12 7v5l3 2',
 save:'M4 4h13l3 3v13H4V4Zm3 0v6h9V4M8 20v-6h8v6',
 link:'M10 13a5 5 0 0 0 7 .4l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7-.4l-3 3a5 5 0 0 0 7 7l1.7-1.7',
 scissors:'M8 7a3 3 0 1 1-6 0 3 3 0 0 1 6 0Zm0 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0ZM8 8l13 12M8 16 21 4',
 copy:'M9 8h10a2 2 0 0 1 2 2v10H9V8ZM5 16H3V3h12v2',
 paste:'M9 4H5v17h14V4h-4M9 2h6v5H9V2Z',
 trash:'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7',
 plus:'M12 5v14M5 12h14',
 fit:'M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5',
 globe:'M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18',
 spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z',
 book:'M12 5v16M12 5C8 2 4 3 2 4v15c4-1 7-1 10 2 3-3 6-3 10-2V4c-2-1-6-2-10 1',
 camera:'M8 6l2-3h4l2 3h4a2 2 0 0 1 2 2v11H2V8a2 2 0 0 1 2-2h4Zm4 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
 color:'M12 3c-5 0-9 4-9 9s4 9 9 9h1a2 2 0 0 0 1-3c-1-2 1-3 3-3h2c4-5-1-12-7-12ZM7 9h.01M11 6h.01M16 8h.01M7 14h.01',
 sort:'M8 4v16m-4-4 4 4 4-4M14 5h7M14 10h5M14 15h3',
 transform:'M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4',
 select:'M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5M9 9h6v6H9Z',
 depth:'m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 16l10 5 10-5',
 favorite:'m12 3 2.8 5.8 6.4.9-4.6 4.5 1.1 6.3-5.7-3-5.7 3 1.1-6.3L2.8 9.7l6.4-.9z',back:'m14 6-6 6 6 6',download:'M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4',text:'M5 4h14M12 4v16M8 20h8'
};
const aliases={upload:'share','reference-canvas':'board',restore:'history',novel:'book',generate:'spark',preview:'fit',versions:'history','save-history':'save',attachment:'image','smart-split':'scissors','create-depth':'depth','generate-text':'text','generate-image':'image','generate-video':'video','generate-audio':'audio','add-here':'plus','add-plain':'plus','reference-web':'globe','reference-document':'document','reference-skill':'spark','reference-book':'book',reference:'link','save-as-note':'document','copy-cards':'copy','cut-cards':'scissors','paste-cards':'paste','duplicate-card':'copy','save-as':'download',reveal:'folder','link-upstream':'link',focus:'fit',arrange:'move','arrange-all':'move','delete-cards':'trash','delete-edge':'trash','select-cards':'select',generation:'spark','edit-image':'edit',separate:'audio',trim:'scissors',frame:'camera',cut:'scissors',duplicate:'copy',rename:'edit',delete:'trash',remove:'trash',export:'share','new-note':'document',note:'document','new-folder':'folder','new-board':'board','parse-video':'globe'};
export const originalIconNames=Object.freeze([...new Set([...Object.keys(codes),'back','favorite','exit-preview'])]);
export function originalIcon(name){const glyph={focus:'E81E',preview:'E740','exit-preview':'E73F'}[name];if(glyph)return `<span class="original-icon original-${name}-icon" data-original-glyph="${glyph}" aria-hidden="true">&#x${glyph};</span>`;const path=shapes[aliases[name]||name];return path?`<svg class="original-icon soft-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" shape-rendering="geometricPrecision" aria-hidden="true"><path d="${path}"/></svg>`:'';}
