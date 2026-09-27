
var MatchmakingReconnect = ( function()
{
	var m_elOngoingMatch = $.GetContextPanel();
	var m_bRevivalAutoReconnectIssued = false;
	var REVIVAL_PANORAMA_AUTO_RECONNECT_V1 = true;
	
	var _Init = function()
	{
		_UpdateState();
		_SetUpButtons();

		m_elOngoingMatch.OnPropertyTransitionEndEvent = function ( panelName, propertyName )
		{
			if( m_elOngoingMatch.id === panelName && propertyName === 'opacity' )
			{
				                                         
				if( m_elOngoingMatch.visible === true && m_elOngoingMatch.BIsTransparent() )
				{
					                                               
					m_elOngoingMatch.visible = false;
					return true;
				}
			}

			return false;
		};
	};

	var _UpdateState = function()
	{
		var bHasOnGoingMatch = CompetitiveMatchAPI.HasOngoingMatch();
		
		m_elOngoingMatch.SetHasClass( 'hidden', !bHasOnGoingMatch );

		// Revival late joins are published as an ongoing match by the GC.
		// Use the game's own reconnect API immediately. The engine then requests
		// ClientRequestJoinServerData, which the revival GC answers with the live
		// server address + reservation rather than starting another ACCEPT flow.
		if ( !bHasOnGoingMatch )
		{
			m_bRevivalAutoReconnectIssued = false;
			return;
		}

		if ( !m_bRevivalAutoReconnectIssued )
		{
			m_bRevivalAutoReconnectIssued = true;
			$.Msg( '[REVIVAL] REVIVAL_PANORAMA_AUTO_RECONNECT_V1 reconnecting to ongoing match' );
			CompetitiveMatchAPI.ActionReconnectToOngoingMatch();
		}
	};

	var _SetUpButtons = function()
	{
		var btnReconnect = $.GetContextPanel().FindChildInLayoutFile( 'MatchmakingReconnect' );
		btnReconnect.SetPanelEvent( 'onactivate', function()
		{
			CompetitiveMatchAPI.ActionReconnectToOngoingMatch();
			$.DispatchEvent( 'PlaySoundEffect', 'UIPanorama.generic_button_press', 'MOUSE' );
		} );

		var btnAbandon = $.GetContextPanel().FindChildInLayoutFile( 'MatchmakingAbandon' );
		btnAbandon.SetPanelEvent( 'onactivate', function()
		{
			CompetitiveMatchAPI.ActionAbandonOngoingMatch();
			$.DispatchEvent( 'PlaySoundEffect', 'UIPanorama.generic_button_press', 'MOUSE' );
		} );
	};

	return {
		Init: _Init,
		UpdateState: _UpdateState
	};
} )();

( function()
{
	MatchmakingReconnect.Init();

	$.RegisterForUnhandledEvent( "PanoramaComponent_Lobby_MatchmakingSessionUpdate", MatchmakingReconnect.UpdateState );
	
	                                                                                                                             
	$.RegisterForUnhandledEvent( 'PanoramaComponent_GC_Hello', MatchmakingReconnect.UpdateState );
} )();