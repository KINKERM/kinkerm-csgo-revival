"use strict";

var m_MissionActivateTimer = null;

var EnsureHostAndMatchmakingStoppedIfNeeded = function()
{
	                                                          
	if ( LobbyAPI.IsSessionActive() && !LobbyAPI.BIsHost() )
	{	                                                                                                                                       
		LobbyAPI.CloseSession();
	}
	
	                                            
	if ( LobbyAPI.IsSessionActive() )
	{
		var settingsGame = LobbyAPI.GetSessionSettings().game;
		if ( settingsGame && settingsGame.mmqueue )
		{
			LobbyAPI.StopMatchmaking();
		}

		settingsGame = LobbyAPI.GetSessionSettings().game;
		if ( settingsGame && settingsGame.mmqueue )
		{	                                          
			return false;
		}
	}

	return true;
}

var SetupPopup = function()
{
	                                                     
	EnsureHostAndMatchmakingStoppedIfNeeded();
	                                                                         

                      
    var strMsg = $.GetContextPanel().GetAttributeString( "message", "(not found)" );
    $.GetContextPanel().SetDialogVariable( "message", strMsg );

                             
    var spinnerVisible = $.GetContextPanel().GetAttributeInt( "spinner", 0 );
    $( "#Spinner" ).SetHasClass( "SpinnerVisible", spinnerVisible );
    
    m_MissionActivateTimer = $.Schedule( 11, PanelTimedOut );
    $.Schedule( 1, LaunchMission );

    $.RegisterForUnhandledEvent( 'PanoramaComponent_MyPersona_InventoryUpdated', OnInventoryUpdated );
};

var PanelTimedOut = function()
{
                                                     
    m_MissionActivateTimer = null;
    $.DispatchEvent( 'UIPopupButtonClicked', '' );

    UiToolkitAPI.ShowGenericPopupOk(
        $.Localize( '#SFUI_SteamConnectionErrorTitle' ),
        $.Localize( '#SFUI_Steam_Error_LinkUnexpected' ),
        '',
        function()
        {
        },
        function()
        {
        }
    );
};

var _CancelMissionActivateTimer  = function()
{
    if ( m_MissionActivateTimer )
    {
        $.CancelScheduled( m_MissionActivateTimer );
        m_MissionActivateTimer = null;
    }
};

function OnInventoryUpdated ()
{
    LaunchMission();
}


function _InterruptToGetGameModeFlags ( mode )
{

    function _Callback ( value, resumeMatchmakingHandle = '' )
    {
        GameInterfaceAPI.SetSettingString( 'ui_playsettings_flags_official' + '_' + mode, value );

        LaunchMission();
    }

    function _CancelCallback ( unused1, unused2 )
    {
        _ClosePopUp();
        $.DispatchEvent( 'UIPopupButtonClicked', '' );
    }

    var callback = UiToolkitAPI.RegisterJSCallback( _Callback );
    var cancelCallback = UiToolkitAPI.RegisterJSCallback( _CancelCallback );

    UiToolkitAPI.ShowCustomLayoutPopupParameters( '', 'file://{resources}/layout/popups/popup_play_gamemodeflags.xml',
        '&callback=' + callback +
        '&cancelcallback=' + cancelCallback +
        '&textToken=' + '#play_settings_' + mode + '_dialog' +
        GameModeFlags.GetOptionsString( mode ) +
        '&currentvalue=' + 0,
    );
    
}

function LaunchMission ()
{
    var nRequestedMissonCardId = $.GetContextPanel().GetAttributeInt( "requestedMissonCardId", 0 );
    var nSeasonAccess = $.GetContextPanel().GetAttributeInt( "seasonAccess", 0 );
    var QuestItemID = $.GetContextPanel().GetAttributeString( "questItemID", '0' );
    var nQuestId = parseInt( InventoryAPI.GetItemAttributeValue( QuestItemID, "quest id" ) ) || 0;

    if ( !nRequestedMissonCardId || !nSeasonAccess || nSeasonAccess < 0 || !nQuestId )
    {
        $.Msg( '[revival operation] invalid selection card=' + nRequestedMissonCardId +
            ' season=' + nSeasonAccess + ' quest=' + nQuestId );
        $.DispatchEvent( 'UIPopupButtonClicked', '' );
        return;
    }

    var iActiveMissionCard = MissionsAPI.GetSeasonalOperationMissionCardActiveIdx( nSeasonAccess );
    var bMissionCardMatches = iActiveMissionCard >= 0 &&
        MissionsAPI.GetSeasonalOperationMissionCardDetails( nSeasonAccess, iActiveMissionCard ).id === nRequestedMissonCardId;

    // Persist the mission through the real GC request. The revival's custom
    // SeasonalOperations update is not mirrored back through the old native
    // MissionsAPI cache reliably, so after one short GC tick continue anyway.
    if ( !$.GetContextPanel().GetAttributeInt( 'revivalMissionRequested', 0 ) )
    {
        $.GetContextPanel().SetAttributeInt( 'revivalMissionRequested', 1 );

        // LOCAL revival bridge: write the exact selection into a tiny console
        // logfile. csgo_gc consumes and deletes it before MatchmakingStart.
        // Keep Valve's native request too, but do not depend on its retired
        // backend/cache path to persist the mission.
        var revivalSelection = 'REVIVAL_MISSION_SELECT_V1 ' +
            nSeasonAccess + ' ' + nRequestedMissonCardId + ' ' + nQuestId;
        GameInterfaceAPI.ConsoleCommand(
            'con_logfile "revival_mission_select.log"; echo ' +
            revivalSelection + '; con_logfile ""' );

        $.Msg( '[revival operation] ' + revivalSelection );
        MissionsAPI.ActionRequestSeasonalOperationMissionCardID(
            nSeasonAccess, nRequestedMissonCardId );

        $.Schedule( 0.35, LaunchMission );
        return;
    }

    _CancelMissionActivateTimer();

    var bOnlyActivateMission = $.GetContextPanel().GetAttributeString( 'activateonly', 'false' ) === 'true' ? true : false;
        if ( bOnlyActivateMission )
        {
            _ClosePopUp();
            $.DispatchEvent( 'UIPopupButtonClicked', '' );
            return;
        }
        
		                                                  
		if ( !EnsureHostAndMatchmakingStoppedIfNeeded() )
			return;

        var gameMode = InventoryAPI.GetQuestGameMode( QuestItemID );
        var gameType = '';
		var mapGroup = InventoryAPI.GetQuestMapGroup( QuestItemID );
		if ( gameMode === 'survival' )
		{	                                                                             
			mapGroup = GameInterfaceAPI.GetSettingString( 'ui_playsettings_maps_official_' + gameMode );
		}
        if ( !mapGroup )
		{	                                                                               
			var strIndividualMap = InventoryAPI.GetQuestMap( QuestItemID );
			if ( strIndividualMap )
			{	                                             
				mapGroup = 'mg_' + strIndividualMap;
			}
			else
			{	                                                                                 
				mapGroup = GameInterfaceAPI.GetSettingString( 'ui_playsettings_maps_official_' + gameMode );
			}
        }

		var gameModeFlags = GameInterfaceAPI.GetSettingString( 'ui_playsettings_flags_official_' + gameMode );
		gameModeFlags = gameModeFlags ? parseInt( gameModeFlags ) : 0;

		var questGameModeFlags = InventoryAPI.GetQuestGameModeFlags( QuestItemID );
		if ( questGameModeFlags )
		{	                                                                               
			gameModeFlags = questGameModeFlags;
		}
        if ( gameMode === 'competitive' )
        {
            gameModeFlags = 16;
        }

                                           
        if ( GameModeFlags.DoesModeUseFlags( gameMode ) && gameModeFlags == 0)
        {
            _InterruptToGetGameModeFlags( gameMode );

            return;
        }

                                                                               
        var cfg = GameTypesAPI.GetConfig();
        for ( var type in cfg.gameTypes )
        {
            for ( var mode in cfg.gameTypes[ type ].gameModes )
            {
                if ( mode === gameMode )
                {
                    gameType = type;
                    break;
                }
            }
            if ( gameType )
            {
                break;
            }
        }

                                                                                                           
        if ( mapGroup.startsWith( 'mg_skirmish_' ) )
        {
            gameType = gameMode = 'skirmish';
        }
        
        if ( !LobbyAPI.IsSessionActive() )
        {
            LobbyAPI.CreateSession();
        }

        if ( LobbyAPI.IsSessionActive() )
        {
			                                                                                                 
			
			var bStartMatchmaking = true;
			var sStageMatchmaking = '';
			                                                                                   
			                                         
			    
			   	                        
			   	                                                                            
			    
			
			var settings = {
                update: {
                    Options: {
                        action: "custommatch",
                        server: "official"
                    },
                    Game: {
                        mode: gameMode,
                        type: gameType,
                        mapgroupname: mapGroup,
                        questid: nQuestId,
                        gamemodeflags: gameModeFlags,
                    },
				},
				delete: {
					Options: {
						challengekey: 1
					}
				}
            };

            LobbyAPI.UpdateSessionSettings( settings );

            if ( ( gameType === 'cooperative' ) && ( PartyListAPI.GetCount() <= 1 ) )
            {
                                                          
                PartyListAPI.SessionCommand( 'MakeOnline', '' );
                PartyListAPI.SessionCommand( 'Game::ChatReportGreen', 'run local green #SFUI_QMM_ERROR_PartyRequired2' );
                bStartMatchmaking = false;
            }
            
            if ( bStartMatchmaking )
            {
                LobbyAPI.StartMatchmaking( '', '', '', sStageMatchmaking );
            }

            _ClosePopUp();
			$.DispatchEvent( 'UIPopupButtonClicked', '' );
			$.DispatchEvent( 'OpenPlayMenu', '' );
        }
}

var _ClosePopUp = function()
{
    $.DispatchEvent( 'CloseOperationHub' );
}
