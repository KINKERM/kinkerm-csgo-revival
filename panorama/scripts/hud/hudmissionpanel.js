'use strict';

var HudMissionPanel = ( function() {

	var _m_cp = $.GetContextPanel();
	var _m_missionId = undefined;
	var _m_elMission = null;

	// Revival HUD fallback: Riptide is no longer Valve's live Operation, so the
	// retail active-quest cache can stay at 0. Read the quest directly from the
	// owned Riptide coin that the revival GC already updates (attribute 168).
	var _GetRevivalActiveQuestID = function()
	{
		var q = parseInt( GameStateAPI.GetActiveQuestID() ) || 0;
		if( q ) return q;
		var defs = OperationUtil.GetCoinDefIdxArray();
		for( var d = 0; d < defs.length; d++ )
		{
			var name = InventoryAPI.GetItemDefinitionName( InventoryAPI.GetFauxItemIDFromDefAndPaintIndex( defs[d], 0 ) );
			if( !name ) continue;
			InventoryAPI.SetInventorySortAndFilters( 'inv_sort_age', false, 'item_definition:' + name, '', '' );
			if( !InventoryAPI.GetInventoryCount() ) continue;
			q = parseInt( InventoryAPI.GetItemAttributeValue( InventoryAPI.GetInventoryItemIDByIndex( 0 ), 'quest id' ) ) || 0;
			if( q )
			{
				$.Msg( '[revival operation hud] coin quest=' + q );
				return q;
			}
		}
		$.Msg( '[revival operation hud] no active quest' );
		return 0;
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
		$.Msg( '[revival operation hud] update=' + _m_missionId );
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

                                                                                                               
                                                                                                                         