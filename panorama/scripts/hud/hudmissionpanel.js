'use strict';

var HudMissionPanel = ( function() {

	var _m_cp = $.GetContextPanel();
	var _m_missionId = undefined;
	var _m_elMission = null;

	// Revival fallback: the old native Operation cache can leave
	// GameStateAPI.GetActiveQuestID() at zero even after the custom GC has
	// persisted the selected Riptide quest. The mission popup stores the exact
	// quest in lobby session settings before matchmaking, so the HUD can use it.
	var _GetRevivalActiveQuestID = function()
	{
		var nativeQuest = parseInt( GameStateAPI.GetActiveQuestID() ) || 0;
		if( nativeQuest > 0 )
			return nativeQuest;

		// Read the exact quest directly from the owned Riptide coin. This uses the
		// same inventory filter path as OperationUtil, so it does not depend on the
		// retired native active-season cache or on lobby settings surviving connect.
		var defs = OperationUtil.GetCoinDefIdxArray();
		for( var d = 0; d < defs.length; d++ )
		{
			var faux = InventoryAPI.GetFauxItemIDFromDefAndPaintIndex( defs[d], 0 );
			var defName = InventoryAPI.GetItemDefinitionName( faux );
			if( !defName ) continue;
			InventoryAPI.SetInventorySortAndFilters( 'inv_sort_age', false, 'item_definition:' + defName, '', '' );
			var count = InventoryAPI.GetInventoryCount();
			for( var i = 0; i < count; i++ )
			{
				var owned = InventoryAPI.GetInventoryItemIDByIndex( i );
				var season = parseInt( InventoryAPI.GetItemAttributeValue( owned, 'season access' ) ) || 0;
				var coinQuest = parseInt( InventoryAPI.GetItemAttributeValue( owned, 'quest id' ) ) || 0;
				if( season === 10 && coinQuest > 0 )
				{
					$.Msg( '[revival operation hud] source=coin quest=' + coinQuest );
					return coinQuest;
				}
			}
		}

		var settings = LobbyAPI.GetSessionSettings();
		var game = settings && settings.game ? settings.game : null;
		var lobbyQuest = game ? ( parseInt( game.questid ) || 0 ) : 0;
		$.Msg( '[revival operation hud] native=0 coin=0 lobby=' + lobbyQuest );
		return lobbyQuest;
	}

	var _OnMatchStart = function()
	{
		                                                                      	
		if ( GameStateAPI.GetGameModeInternalName( false ) === "survival" )
		{
			_HideMissionPanel();
			return;
		}

		_UpdateMission();

		                                                                                
		                         
		if( isCompetitiveNotSurvival() )
		{
			$.Schedule( 5, _HideMissionPanel );
		}
	}

	var _UpdateProgress = function()
	{
		$.Schedule( 0, _UpdateMission );
	}

	var _UpdateMission = function()
	{
		_m_missionId = _GetRevivalActiveQuestID();
		$.Msg( '[revival operation hud] update quest=' + _m_missionId + ' map=' + GameStateAPI.GetMapBSPName() );
		if( !_m_missionId || _m_missionId === 0 || _m_missionId === '0' || GameStateAPI.GetMapBSPName() === 'lobby_mapveto' )
		{
			_DeleteMissionPanel();
			return;
		}

		var oMissionDetails = OperationUtil.GetMissionDetails( _m_missionId );
		                                        
		if( oMissionDetails.isReplayable )
		{
			_DeleteMissionPanel();
			return;
		}

		_m_elMission = OperationMission.CreateMission( 
			_m_cp, 
			oMissionDetails,
			false );

		var isunlocked = true;
		var elMissionCard = null;
		var showCompleteWarning = false;
		var isList = oMissionDetails.missonType === 'sequential' || oMissionDetails.missonType === 'checklist';
		OperationMission.UpdateMissionDisplay( _m_elMission, oMissionDetails, isunlocked, elMissionCard, isList);

		if( isList )
		{
			var aIncomplete = oMissionDetails.aSubQuests.filter( element => ( element.nUncommitted < 1 ) && ( element.nsubQuestPointsRemaining > 0 ));

			OperationMission.HudIncompleteSubMissions( 
				_m_elMission,
				aIncomplete
			);

			showCompleteWarning = aIncomplete.length === 0;
		}
		else if( oMissionDetails.missonType === 'or' )
		{
			oMissionDetails.aSubQuests.forEach( element => {
				if(( ( element.nUncommitted + element.nEarned ) === element.nGoal))
				{
					showCompleteWarning = true;
				}
			});
		}

		_m_cp.SetHasClass( 'show', true );
		_m_cp.SetHasClass( 'show-uncommitted-warning', showCompleteWarning );
		_m_cp.SetHasClass( 'short', oMissionDetails.missonType === 'sequential' );
	}

	var _DeleteMissionPanel = function()
	{
		                                                                             
		var _m_elMission = _m_cp.FindChildInLayoutFile( _m_missionId );
		if( _m_elMission && _m_elMission.IsValid() )
		{
			_m_elMission.DeleteAsync( 0.0 );
		}
		_HideMissionPanel();
	}

	var _HideMissionPanel = function()
	{
		_m_cp.SetHasClass( 'show', false );
	}

	var _OnReceiveMVP = function()
	{
		if( _m_cp.BHasClass( 'show') === false )
		{
			_UpdateMission();
		}
	};

	var _OnRoundFreezeTimeEnd = function()
	{
		                                             
		if( isCompetitiveNotSurvival() )
		{
			_HideMissionPanel();
		}
	};

	var isCompetitiveNotSurvival = function()
	{
		                                                                          
		return GameStateAPI.IsQueuedMatchmaking() && GameStateAPI.GetGameModeInternalName( false ) !== "survival";
	};

	var _SurvivalMatchStart = function( SurvivalPhase )
	{
		if ( SurvivalPhase === 5 )
		{
			_UpdateMission();
		}
	};

	return {
		OnMatchStart: _OnMatchStart,
		LevelTransitionStart: _DeleteMissionPanel,
		UpdateProgress: _UpdateProgress,
		OnReceiveMVP: _OnReceiveMVP,
		OnRoundFreezeTimeEnd: _OnRoundFreezeTimeEnd,
		SurvivalMatchStart: _SurvivalMatchStart
	};
} )();

(function()
{
	$.Msg( '[revival operation hud] script loaded' );
	$.RegisterForUnhandledEvent( "GameState_OnMatchStart", HudMissionPanel.OnMatchStart );
	$.RegisterForUnhandledEvent( "GameState_LevelInitPreEntity", HudMissionPanel.LevelTransitionStart );
	$.RegisterForUnhandledEvent( "OnQuestProgressMade", HudMissionPanel.UpdateProgress );
	$.RegisterForUnhandledEvent( 'OnRoundMVPShown', HudMissionPanel.OnReceiveMVP );
	$.RegisterForUnhandledEvent( 'OnRoundFreezeTimeEnd', HudMissionPanel.OnRoundFreezeTimeEnd );
	$.RegisterForUnhandledEvent( 'SurvivalSpawnSelectModeChange', HudMissionPanel.SurvivalMatchStart );
})();

                                                                                                               
                                                                                                                         