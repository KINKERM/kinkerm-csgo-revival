'use strict';

var RevivalNews = [
	{
		date: '24 Sep 2026',
		title: 'CS:GO Revival',
		description: 'The revival client now owns its main-menu news feed instead of showing Counter-Strike 2 news from Valve.',
		imageUrl: '',
		link: ''
	},
	{
		date: '24 Sep 2026',
		title: 'Operation Riptide Restored',
		description: 'Operation pass activation, stars, the Riptide shop, rewards, mission state, and coin progression are being restored through the revival Game Coordinator.',
		imageUrl: '',
		link: ''
	},
	{
		date: '24 Sep 2026',
		title: 'Official Matchmaking Returns',
		description: 'The Online / Official Play entry is back. Competitive and Wingman matchmaking are being wired to revival-hosted dedicated servers.',
		imageUrl: '',
		link: ''
	}
];

var NewsPanel = (function () {

	var _GetRssFeed = function()
	{
		// Do not ask Valve's live BlogAPI for news; that returns modern CS2 posts.
		// Keep the legacy main-menu presentation but own the actual feed locally.
		$.Schedule( 0.0, function()
		{
			_OnRssFeedReceived( { items: RevivalNews } );
		} );
	}

	var _OnRssFeedReceived = function( feed )
	{
		if( $.GetContextPanel().BHasClass( 'news-panel--hide-news-panel' ) )
		{
			return;
		};

		var elLister = $.GetContextPanel().FindChildInLayoutFile( 'NewsPanelLister' );

		if ( elLister === undefined || elLister === null || !feed || !feed.items )
			return;

		elLister.RemoveAndDeleteChildren();

		feed.items.forEach( function( item, i )
		{
			var elEntry = $.CreatePanel( 'Panel', elLister, 'NewEntry' + i, {
				acceptsinput: true
			} );

			if ( i === 0 )
				elEntry.AddClass( 'new' );

			elEntry.BLoadLayoutSnippet( 'news-full-entry' );
			var elImage = elEntry.FindChildInLayoutFile( 'NewsHeaderImage' );
			if ( item.imageUrl )
			{
				elImage.SetImage( item.imageUrl );
			}
			else
			{
				elImage.SetImage( 'file://{images}/store/default-news.png' );
			}

			var elEntryInfo = $.CreatePanel( 'Panel', elEntry, 'NewsInfo' + i );
			elEntryInfo.BLoadLayoutSnippet( 'news-info' );

			elEntryInfo.SetDialogVariable( 'news_item_date', item.date || '' );
			elEntryInfo.SetDialogVariable( 'news_item_title', item.title || '' );
			elEntryInfo.SetDialogVariable( 'news_item_body', item.description || '' );

			elEntry.FindChildInLayoutFile( 'NewsEntryBlurTarget' ).AddBlurPanel( elEntryInfo );

			if ( item.link )
			{
				elEntry.SetPanelEvent( 'onactivate', function( link, panel )
				{
					SteamOverlayAPI.OpenURL( link );
					panel.RemoveClass( 'new' );
				}.bind( undefined, item.link, elEntry ) );
			}
		} );
	};

	return {
		GetRssFeed: _GetRssFeed,
		OnRssFeedReceived: _OnRssFeedReceived,
	};
})();

(function()
{
	NewsPanel.GetRssFeed();
})();
