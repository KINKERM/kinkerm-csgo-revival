'use-strict';
var CapabilityDecodable = ( function()
{
	var a = [];
	var b = [ 'ScrollList', 'ScrollListMagnified'];
	var c = '';
	var d = '';
	var e = '';
	var f = $.GetContextPanel();
	var g = '';
	var h = '';
	var i = false;
	var j = '';
	var k = '';
	var l = null;
	var m = true;
	var n = '';
	var o = false;
	var p = false;
	var q = null;
	var _Init = function()
	{
		function GetItemVarsFromMsg()
		{
			var idList = strMsg.split( ',' );
			return { key: idList[ 0 ], case: idList[ 1 ] };
		}
		function SetsItemVarsFromMsg()
		{
			var oData = GetItemVarsFromMsg();
			g = oData.key;
			c = oData.case;
		}
		var strMsg = $.GetContextPanel().GetAttributeString( "key-and-case", "" );
		o = $.GetContextPanel().GetAttributeString( "isxraymode", "no" ) === 'yes' ? true : false;
		m = ( $.GetContextPanel().GetAttributeString( 'allowtointeractwithlootlistitems', 'true' ) === 'true' ) ? true : false;
		p = ( $.GetContextPanel().GetAttributeString( 'bluroperationpanel', 'false' ) === 'true' ) ? true : false;
		if ( p )
		{
			$.DispatchEvent( 'BlurOperationPanel' );
		}
		if ( o )
		{
			f.SetHasClass( 'popup-in-xray', o ); 
			var oData = ItemInfo.GetItemsInXray();
			d = oData.reward;
			if ( d )
			{
				{
					if ( InventoryAPI.IsFauxItemID( d ) )
					{
						var elPopup = UiToolkitAPI.ShowGenericPopupOk( '#popup_xray_first_use_title', '#popup_xray_first_use_desc', '', function() { } );
						elPopup.FindChildInLayoutFile( 'MessageLabel' ).html = true;
						var id = InventoryAPI.GetFauxItemIDFromDefAndPaintIndex( d, 0 );
						elPopup.SetDialogVariable( 'itemname', ItemInfo.GetName( d ) );
						elPopup.FindChildInLayoutFile( 'MessageLabel' ).text = $.Localize( '#popup_xray_first_use_desc', elPopup );
					}
					else if( $.GetContextPanel().GetAttributeString( "showxraypopup", "no" ) === 'yes' )
					{
						UiToolkitAPI.ShowGenericPopupOk( '#popup_xray_in_use_title', '#popup_xray_in_use_desc', '', function() { } );
					}
				}
				c = oData.case;
			}
			else
			{
				SetsItemVarsFromMsg();
			}
			if ( !GetItemVarsFromMsg().key )
			{
				var keyId = ItemInfo.GetKeyForCaseInXray( c );
				if ( keyId )
				{
					g = keyId;
				}
			}
			else
			{
				g = GetItemVarsFromMsg().key;
			}
		}
		else
		{
			SetsItemVarsFromMsg();
		}
		n = $.GetContextPanel().GetAttributeString( 'extrapopupfullscreenstyle', '' );
		if ( n )
		{
			var elPopUpInspectFullScreenHostContainer = $.GetContextPanel().FindChildInLayoutFile( 'PopUpInspectFullScreenHostContainer' );
			elPopUpInspectFullScreenHostContainer.AddClass( n );
		}
		if ( !g )
		{
			var associatedItemCount = InventoryAPI.GetAssociatedItemsCount( c );
			if ( !InventoryAPI.IsItemInfoValid( c ) )
			{
				return;
			}
			j = $.GetContextPanel().GetAttributeString( "storeitemid", "" );
			if ( ( associatedItemCount === 0 || !associatedItemCount ) && !j )
			{
				i = true;
			}
			else if ( !j )
			{
				h = InventoryAPI.GetAssociatedItemIdByIndex( c, 0 );
			}
		}
		else
		{
			if ( !InventoryAPI.IsItemInfoValid( g ) )
			{
				return;
			}
		}
		_SetUpPanelElements();
		$.DispatchEvent( 'CapabilityPopupIsOpen', true );
	};
	var _SetUpPanelElements = function()
	{
		if ( !g )
		{
			$.GetContextPanel().SetAttributeString( 'asyncworkitemwarning', 'no' );
			$.GetContextPanel().SetAttributeString( 'asyncactiondescription', 'no' );
			if ( d )
			{
				$.GetContextPanel().SetAttributeString( 'allowxraypurchase', 'yes' );
			}
		}
		else
		{
			$.GetContextPanel().SetAttributeString( 'toolid', g );
			$.GetContextPanel().SetAttributeString( 'asyncworkitemwarning', 'yes' );
			$.GetContextPanel().SetAttributeString( 'asyncactiondescription', 'yes' );
			if ( d )
			{
				$.GetContextPanel().SetAttributeString( 'allowxrayclaim', 'yes' );
			}
		}
		if ( i )
		{
			if ( d )
			{
				$.GetContextPanel().SetAttributeString( 'allowxrayclaim', 'yes' );
			}
			$.GetContextPanel().SetAttributeString( 'decodeablekeyless', 'true' );
			$.GetContextPanel().SetAttributeString( 'asyncworkitemwarning', 'yes' );
			$.GetContextPanel().SetAttributeString( 'asyncactiondescription', 'yes' );
		}
		var sRestriction = j ? '' : InventoryAPI.GetDecodeableRestriction( c );
		if ( sRestriction !== 'restricted' && sRestriction !== 'xray' || ( o && sRestriction === 'xray' ) )
		{
			_ShowPurchase( ( g ) ? '' : h );
			var slot = ItemInfo.GetSlot( c );
			if ( slot == "musickit" )
			{
				InventoryAPI.PlayItemPreviewMusic( c );
			}
		}
		_SetupHeader( c );
		_SetupDescription( c );
		_SetUpAsyncActionBar( c );
		if ( o )
		{
			_SetUpXrayPanel();
		}
		else
		{
			_SetCaseModelImage( c, 'PopUpInspectModelOrImage' );
			_SetCaseModelCamera( 1, false );
			if ( !ItemInfo.ItemMatchDefName( c, 'spray' ) && !ItemInfo.ItemDefinitionNameSubstrMatch( c, 'tournament_pass_' ) )
			{
				_PlayCaseModelAnim( 'fall' );
				_PlayContainerSound( c, 'fall' );
			}
			_SetLootListItems( c, g );
		}
	};
	var _SetupHeader = function( caseId )
	{
		var elCapabilityHeaderPanel = $.GetContextPanel().FindChildInLayoutFile( 'PopUpCapabilityHeader' );
		CapabiityHeader.Init( elCapabilityHeaderPanel, caseId, _GetSettingCallback );
	};
	var _SetupDescription = function( caseId )
	{
		var elPanel = $.GetContextPanel().FindChildInLayoutFile( 'InspectItemDesc' );
		var count = ItemInfo.GetLootListCount( caseId );
		if ( count === 0 && j )
		{
			elPanel.visible = true;
			elPanel.text = InventoryAPI.GetItemDescription( caseId );
		}
		else
		{
			elPanel.visible = false;
		}
	};
	var _GetSettingCallback = function( settingname, defaultvalue )
	{
		return f.GetAttributeString( settingname, defaultvalue );
	};
	var _SetCaseModelImage = function( caseId, PanelId )
	{
		q = $.GetContextPanel().FindChildInLayoutFile( PanelId );
		InspectModelImage.Init( q, caseId, _GetSettingCallback );
	};
	var _PlayCaseModelAnim = function( anim )
	{
		var elModel = q.FindChildInLayoutFile( 'InspectItemModel' );
		elModel.PlaySequence( anim, true );
	};
	var _SetCaseModelCamera = function( preset, shouldTransition )
	{
		var elModel = q.FindChildInLayoutFile( 'InspectItemModel' );
		elModel.SetCameraPreset( preset, shouldTransition );
	};
	var _SetUpAsyncActionBar = function( itemId )
	{
		var elAsyncActionBarPanel = $.GetContextPanel().FindChildInLayoutFile( 'PopUpInspectAsyncBar' );
		InspectAsyncActionBar.Init(
			elAsyncActionBarPanel,
			itemId,
			_GetSettingCallback
		);
	};
	var _ShowPurchase = function( h )
	{
		var elPurchase = $.GetContextPanel().FindChildInLayoutFile( 'PopUpInspectPurchaseBar' );
		InpsectPurchaseBar.Init(
			elPurchase,
			h,
			_GetSettingCallback
		);
	};
	var _SetLootListItems = function( caseId, keyId )
	{
		var count = ItemInfo.GetLootListCount( caseId );
		var elLootList = $.GetContextPanel().FindChildInLayoutFile( 'DecodableLootlist' );
		if ( count === 0 )
		{
			_ShowHideLootList( false );
			return;
		}
		var elImage = q.FindChildInLayoutFile( 'InspectItemImage' );
		elImage.AddClass( 'y-offset' );
		_ShowHideLootList( true );
		_SetLootlistHintText( caseId, count );
		for ( var i = 0; i < count; i++ )
		{
			var itemid = ItemInfo.GetLootListItemByIndex( caseId, i );
			var elItem = elLootList.FindChildInLayoutFile( itemid );
			if ( !elItem )
			{
				var elItem = $.CreatePanel( 'Panel', elLootList, itemid );
				elItem.SetAttributeString( 'itemid', itemid );
				elItem.BLoadLayoutSnippet( 'LootListItem' );
				_UpdateLootListItemInfo( elItem, itemid, caseId );
				var funcActivation = m ? _OnActivateLootlistTile : _OnActivateLootlistTileDummy;
				elItem.SetPanelEvent( 'onactivate', funcActivation.bind( undefined, itemid, caseId, keyId ) );
				elItem.SetPanelEvent( 'oncontextmenu', funcActivation.bind( undefined, itemid, caseId, keyId ) );
				if ( i === 0 && m )
				{
					$.GetContextPanel().FindChildInLayoutFile( 'CanDecodableBrowseBtn' ).SetPanelEvent( 'onactivate', callBackFunc.bind( undefined, itemid, caseId, keyId ) );
				}
				if ( itemid !== '0' )
				{
					a.push( {
						id: itemid,
						weight: _GetDisplayWeightForScroll( itemid ),
					} );
				}
			}
		}
	};
	var _OnActivateLootlistTileDummy = function( itemid, caseId, keyId )
	{
	}
	var _OnActivateLootlistTile = function( itemid, caseId, keyId )
	{
		if ( !InventoryAPI.IsValidItemID( itemid ) )
			return;
		InventoryAPI.PrecacheCustomMaterials( itemid );
		var items = [];
		items.push( { label: '#UI_Inspect', jsCallback: callBackFunc.bind( undefined, itemid, caseId, keyId ) } );
		if ( MyPersonaAPI.GetLauncherType() !== "perfectworld" )
		{
			items.push( { label: '#SFUI_Store_Market_Link', jsCallback: _ViewOnMarket.bind( undefined, itemid ) } );
		}
		UiToolkitAPI.ShowSimpleContextMenu( '', 'ControlLibSimpleContextMenu', items );
	};
	var callBackFunc = function( itemid, caseId, keyId )
	{
		$.DispatchEvent( 'ContextMenuEvent', '' );
		_HidePanelForLootlistItemPreview();
		var storeid = ( j ) ? j : '';
		var bluroperationpanel = p ? 'bluroperationpanel=true' : '';
		var additionalParams = _GetSettingCallback( 'inspectonly', 'false' ) === 'true' ? 'inspectonly=true,' : '';
		additionalParams = _GetSettingCallback( 'asyncworkbtnstyle', 'positive' ) === 'hidden' ? additionalParams + 'asyncworkbtnstyle=hidden' : '';
		additionalParams = p ? additionalParams + ',' + 'bluroperationpanel=true' : '';
		$.DispatchEvent(
			"LootlistItemPreview",
			itemid,
			keyId +
			',' + caseId +
			',' + storeid +
			',' + bluroperationpanel +
			',' + n +
			',' + additionalParams
		);
	};
	var _ViewOnMarket = function( id )
	{
		SteamOverlayAPI.OpenURL( ItemInfo.GetMarketLinkForLootlistItem( id ) );
		StoreAPI.RecordUIEvent( "ViewOnMarket" );
	};
	var _GetDisplayWeightForScroll = function( itemid )
	{
		var rarityVal = InventoryAPI.GetItemRarity( itemid );
		var displayItemWeight = [ 150000, 30000, 6000, 1250, 250, 50, 10];
		return displayItemWeight[ rarityVal];
	};
	var _UpdateLootListItemInfo = function( elItem, itemid, caseId )
	{
		if ( itemid == '0' )
		{
			k = InventoryAPI.GetLootListUnusualItemImage( caseId ) + ".png";
			_UpdateUnusualItemInfo( elItem, caseId, k );
		}
		else
		{
			elItem.FindChildInLayoutFile( 'ItemImage' ).itemid = itemid;
			elItem.FindChildInLayoutFile( 'JsRarity' ).style.backgroundColor = ItemInfo.GetRarityColor( itemid );
			ItemInfo.GetFormattedName( itemid ).SetOnLabel( elItem.FindChildInLayoutFile( 'JsItemName' ) );
		}
	};
	var _ShowHideLootList = function( bshow )
	{
		var elLootListContainer = $.GetContextPanel().FindChildInLayoutFile( 'DecodableLootlistContainer' );
		elLootListContainer.SetHasClass( 'hidden', !bshow );
	};
	var _SetLootlistHintText = function( caseId, count )
	{
		var bAllItems = InventoryAPI.GetLootListAllEntriesAreAdditionalDrops( caseId );
		$.GetContextPanel().FindChildInLayoutFile( 'CanDecodableDesc' ).visible = !bAllItems;
		if ( count > 1 || bAllItems )
		{
			$.GetContextPanel().FindChildInLayoutFile( 'CanDecodableDescMulti' ).SetDialogVariableInt( 'num_items', count );
			$.GetContextPanel().FindChildInLayoutFile( 'CanDecodableDescMulti' ).visible = ( count > 1 && bAllItems );
		}
	}
	var _UpdateUnusualItemInfo = function( elItem, caseId, unusualItemImagePath )
	{
		elItem.FindChildInLayoutFile( 'ItemImage' ).SetImage( "file://{images_econ}/" + unusualItemImagePath );
		elItem.FindChildInLayoutFile( 'JsRarity' ).AddClass( 'popup-decodable-wash-color-unusual' );
		var elBg = elItem.FindChildInLayoutFile( 'ItemTileBg' );
		if ( elBg )
			elBg.AddClass( 'popup-decodable-wash-color-unusual-bg' );
		var elName = elItem.FindChildInLayoutFile( 'JsItemName' );
		if ( elName )
			elName.text = InventoryAPI.GetLootListUnusualItemName( caseId );
	};
	var _SetUpCaseOpeningScroll = function()
	{
		_ShowHideLootList( false );
		var elImage = q.FindChildInLayoutFile( 'InspectItemImage' );
		var elCase = null;
		var delay = 0;
		if ( !elImage.BHasClass( 'hidden' ) )
		{
			elImage.RemoveClass( 'y-offset' );
			elCase = elImage;
			delay = 0.1;
		}
		else
		{
			$.Schedule( 1, _PlayCaseModelAnim.bind( undefined, 'open' ) );
			_SetCaseModelCamera( 3, true );
			elCase = q.FindChildInLayoutFile( 'InspectItemModel' );
			delay = 2.3;
		}
		$.Schedule( delay, _ShowScroll.bind( undefined, elCase ) );
	};
	var _ShowScroll = function( elCase )
	{
		var elScroll = $.GetContextPanel().FindChildInLayoutFile( 'DecodableItemsScroll' );
		elScroll.RemoveClass( 'hidden' );
		elCase.AddClass( 'popup-inspect-modelpanel_darken_blur' );
		_FillScrollsWithItems( b );
		$.Schedule( 0.1, _PlayScrollAnim.bind( undefined, b ) );
	};
	var _PlayScrollAnim = function( scrolllists )
	{
		var targetId = 'ItemFromContainer';
		var xOffsetSlackPercent = ( Math.floor( Math.random() * ( ( 90 ) - 10 + 1 ) + 10 ) / 100 );
		scrolllists.forEach( element =>
		{
			var xPos = _GetStopPostion( $.GetContextPanel().FindChildInLayoutFile( element ), targetId, xOffsetSlackPercent );
			var elScroll = $.GetContextPanel().FindChildInLayoutFile( element );
			elScroll.ScrollToFitRegion( xPos, xPos, 0, 0, 3, true, false );
		} );
		var revealDelay = 6;
		$.Schedule( ( revealDelay - 1 ), _PreCacheTextureForNewWeaponInpsect );
		l = $.Schedule( revealDelay, _ShowInspect );
		var itemDefName = ItemInfo.GetItemDefinitionName( c );
		var soundEventName = "container_weapon_ticker";
		if ( itemDefName && itemDefName.indexOf( "sticker" ) != -1 )
		{
			soundEventName = "container_sticker_ticker";
		}
		for ( var i = 0; i < _TickSoundIntervals.length; ++i )
		{
			$.Schedule( _TickSoundIntervals[ i ], _ScrollTick.bind( undefined, soundEventName ) );
		}
	};
	var _TickSoundIntervals = [0, 0.06, 0.12, 0.19, 0.25, 0.31, 0.38, 0.44, 0.50, 0.56, 0.62, 0.69, 0.75, 0.81, 0.88, 0.94, 1, 1.06, 1.12, 1.19, 1.25, 1.31, 1.38, 1.48, 1.35, 1.62, 1.70, 1.79, 1.87, 2, 2.15, 2.31, 2.47, 2.62, 2.77, 2.94, 3.10, 3.34, 3.63, 3.95, 4.38, 5];
	var GOLD_NEAR_MISS_CHANCE = 0.05;
	var _MaybeInsertGoldNearMiss = function( displayItemsList, wonGold )
	{
		if ( wonGold )
			return; 
		if ( Math.random() > GOLD_NEAR_MISS_CHANCE )
			return;
		displayItemsList[ 4 ] = '0'; 
	};
	var _ScrollTick = function( soundEventName )
	{
		$.DispatchEvent( "PlaySoundEffect", soundEventName, "MOUSE" );
	};
	var _GetStopPostion = function( elParent, targetId, xOffsetSlackPercent )
	{
		var elTile = elParent.FindChildInLayoutFile( targetId );
		var tileWidth = elTile.contentwidth;
		return ( elTile.actualxoffset + ( tileWidth * xOffsetSlackPercent ) );
	};
	var _PreCacheTextureForNewWeaponInpsect = function()
	{
		if ( e )
		{
			InventoryAPI.PrecacheCustomMaterials( e );
		}
		if ( d )
		{
			InventoryAPI.PrecacheCustomMaterials( d );
		}
	};
	var _ShowInspect = function()
	{
		l = null;
		if ( e )
		{
			InventoryAPI.SetItemSessionPropertyValue( e, 'recent', '1' );
			InventoryAPI.AcknowledgeNewItembyItemID( e );
			if ( ItemInfo.ItemDefinitionNameSubstrMatch( e, 'tournament_journal_' ) )
			{
				$.Schedule( 0.2, function()
				{
					UiToolkitAPI.ShowCustomLayoutPopupParameters(
						'',
						'file://{resources}/layout/popups/popup_tournament_journal.xml',
						'journalid=' + e
					);
				} );
			}
			else
			{
				$.DispatchEvent( "InventoryItemPreview", e );
			}
			CapabilityDecodable.ClosePopUp();
			var rarityVal = InventoryAPI.GetItemRarity( e );
			var soundEvent = "ItemRevealRarityCommon";
			if ( rarityVal == 4 )
			{
				soundEvent = "ItemRevealRarityUncommon";
			} else if ( rarityVal == 5 )
			{
				soundEvent = "ItemRevealRarityRare";
			} else if ( rarityVal == 6 )
			{
				soundEvent = "ItemRevealRarityMythical";
			} else if ( rarityVal == 7 )
			{
				soundEvent = "ItemRevealRarityLegendary";
			} else if ( rarityVal == 8 )
			{
				soundEvent = "ItemRevealRarityAncient";
			}
			$.DispatchEvent( "PlaySoundEffect", soundEvent, "MOUSE" );
		}
		else
		{
			_TimeoutPopup();
		}
	};
	var _TimeoutPopup = function()
	{
		CapabilityDecodable.ClosePopUp();
		UiToolkitAPI.ShowGenericPopupOk(
			$.Localize( '#SFUI_SteamConnectionErrorTitle' ),
			$.Localize( '#SFUI_InvError_Item_Not_Given' ),
			'',
			function()
			{
			},
			function()
			{
			}
		);
	};
	var _FillScrollsWithItems = function( lists )
	{
		var numTilesInScroll = 38;
		var indexItemsFromContainer = 3;
		var indexStart = ( numTilesInScroll - 3 );
		var totalWeight = 0;
		a.forEach( element =>
		{
			totalWeight += element.weight;
		} );
		var displayItemsList = [];
		for ( var i = 0; i < numTilesInScroll; i++ )
		{
			var itemToAdd = GetItemBasedOnDisplayWeight( totalWeight, a );
			if ( itemToAdd )
				displayItemsList.push( itemToAdd );
		}
		_MaybeInsertGoldNearMiss( displayItemsList, InventoryAPI.IsItemUnusual( e ) );
		lists.forEach( element =>
		{
			var elParent = $.GetContextPanel().FindChildInLayoutFile( element );
			for ( var i = 0; i < displayItemsList.length; i++ )
			{
				var itemId = displayItemsList[ i];
				var tileId = ( i === indexItemsFromContainer ) ? 'ItemFromContainer' : ( i === indexStart ) ? 'ItemStart' : itemId;
				var elTile = $.CreatePanel( 'Panel', elParent, tileId );
				elTile.BLoadLayoutSnippet( 'ScrollItem' );
				_UpdateScrollTile( element, elTile, itemId );
			}
		} );
	};
	var _UpdateScrollTile = function( listId, elTile, itemId )
	{
		if ( listId === 'ScrollListMagnified' )
		{
			elTile.AddClass( 'magnified' );
		}
		itemId = ( elTile.id === 'ItemFromContainer' && e ) ? e : itemId;
		if ( ( itemId === '0' || InventoryAPI.IsItemUnusual( itemId ) ) && k )
		{
			_UpdateUnusualItemInfo( elTile, c, k );
		}
		else
		{
			elTile.FindChildInLayoutFile( 'ItemImage' ).itemid = itemId;
			elTile.FindChildInLayoutFile( 'JsRarity' ).style.washColor = ItemInfo.GetRarityColor( itemId );
			elTile.FindChildInLayoutFile( 'JItemTint' ).style.washColor = ItemInfo.GetRarityColor( itemId );
		}
	};
	var GetItemBasedOnDisplayWeight = function( totalWeight, aItemsInLootlist )
	{
		var weightOfItem = 0;
		var Random = Math.floor( Math.random() * totalWeight );
		for ( var i = 0; i < aItemsInLootlist.length; i++ )
		{
			weightOfItem += aItemsInLootlist[ i ].weight;
			if ( Random <= weightOfItem )
				return aItemsInLootlist[ i ].id;
		}
	};
	var _SetUpCaseOpeningCountdown = function()
	{
		_UpdateOpeningCounter.SetIsGraffiti( _GetContainerType( c ) === 'graffiti' );
		_UpdateOpeningCounter.ShowCounter();
		_UpdateOpeningCounter.UpdateCounter();
		_ShowHideLootList( false );
	};
	var _UpdateOpeningCounter = ( function()
	{
		var counterVal = 6;
		var elCountdown = $.GetContextPanel().FindChildInLayoutFile( 'DecodableCountdown' );
		var elCountdownLabel = elCountdown.FindChildInLayoutFile( 'DecodableCountdownLabel' );
		var elCountdownRadial = elCountdown.FindChildInLayoutFile( 'DecodableCountdownRadial' );
		var timerHandle = null;
		var isGraffitiUnseal = false;
		var _UpdateCounter = function()
		{
			timerHandle = null;
			counterVal = counterVal - 1;
			if ( counterVal === 0 )
			{
				elCountdown.AddClass( 'hidden' );
				_ShowInspect();
			}
			else
			{
				$.DispatchEvent( "PlaySoundEffect", "container_countdown", "MOUSE" );
				elCountdownLabel.text = counterVal;
				if ( !isGraffitiUnseal )
				{
					elCountdownLabel.visible = true;
					elCountdownLabel.RemoveClass( 'popup-countdown-anim' );
					elCountdownLabel.AddClass( 'popup-countdown-anim' );
				}
				else
				{
					elCountdownLabel.visible = false;
				}
				elCountdownRadial.RemoveClass( 'popup-countdown-timer-circle-anim' );
				elCountdownRadial.AddClass( 'popup-countdown-timer-circle-anim' );
				timerHandle = $.Schedule( 1, _UpdateCounter );
			}
		};
		var _ShowCounter = function()
		{
			elCountdown.RemoveClass( 'hidden' );
		};
		var _CancelTimer = function()
		{
			if ( timerHandle )
			{
				$.CancelScheduled( timerHandle );
				timerHandle = null;
			}
		};
		var _SetIsGraffiti = function( isGraffiti )
		{
			isGraffitiUnseal = isGraffiti;
		};
		return {
			UpdateCounter: _UpdateCounter,
			ShowCounter: _ShowCounter,
			CancelTimer: _CancelTimer,
			SetIsGraffiti: _SetIsGraffiti
		};
	} )();
	var _SetUpXrayPanel = function()
	{
		if ( !c )
		{
			return;
		}
		var elActionsPanel = $.GetContextPanel().FindChildInLayoutFile( 'XrayItemsActionPanel' );
		elActionsPanel.AddClass( 'hidden' );
		if ( !d )
		{
			elActionsPanel.RemoveClass( 'hidden' );
			_SetCaseModelImage( c, 'PopUpXrayModelOrImage' );
			var elBtn = $.GetContextPanel().FindChildInLayoutFile( 'ConfirmXray' );
			elBtn.SetPanelEvent( 'onactivate', _OnActivateXray.bind( undefined, elBtn ) );
			$.GetContextPanel().FindChildInLayoutFile( 'PopUpXrayStatusLabel' ).text = $.Localize( "#popup_xray_ready_for_use" );
		}
		else if( d )
		{
			var elHeaderPanel = $.GetContextPanel().FindChildInLayoutFile( 'PopUpInspectHeader' );
			InspectHeader.Init( elHeaderPanel, d, _GetSettingCallback );
			$.GetContextPanel().FindChildInLayoutFile( 'XrayItemsActionPanelItemName' ).RemoveClass( 'hidden' );
			var elImagePanel = $.GetContextPanel().FindChildInLayoutFile( 'PopUpXrayModelOrImageReveal' );
			if ( !elImagePanel.BHasClass( 'popup-xray-reverse-effect' ) )
			{
				elImagePanel.AddClass( 'no-anim' );
				elImagePanel.AddClass( 'popup-xray-reverse-effect' );
				_SetCaseModelImage( d, 'PopUpXrayModelOrImageReveal' );
			}
			$.GetContextPanel().FindChildInLayoutFile( 'PopUpXrayStatusLabel' ).text = $.Localize( "#popup_xray_already_in_use" );
			$.GetContextPanel().FindChildInLayoutFile( 'PopUpXrayStatusDot' ).AddClass( 'in-use' );
		}
		var elXrayPanel = $.GetContextPanel().FindChildInLayoutFile( 'XrayItemsPanel' );
		elXrayPanel.RemoveClass( 'hidden' );
		var aPanels = $.GetContextPanel().FindChildInLayoutFile( 'PopUpXrayBgSquares' ).Children();
		_AnimSquares( aPanels );
	};
	var _OnActivateXray = function( elBtn )
	{
		InventoryAPI.UseTool( c, c );
		elBtn.enabled = false;
		_XrayReveal();
		$.DispatchEvent( 'PlaySoundEffect', 'XrayStart', 'MOUSE' );
	};
	var _XrayReveal = function()
	{
		var revealDelay = 3.5;
		$.Schedule( ( revealDelay - 0.5 ), _PreCacheTextureForNewWeaponInpsect );
		l = $.Schedule( revealDelay, _ShowXrayReward );
		var oData = {
			clipValue: 0,
			lineValue: 100,
			clipPanel: $.GetContextPanel().FindChildInLayoutFile( 'PopUpXrayModelOrImage' ),
			linePanel: $.GetContextPanel().FindChildInLayoutFile( 'PopUpXrayModelOrImageRevealLine' )
		};
		oData.clipPanel.AddClass( 'popup-xray-inverse-effect' );
		$.GetContextPanel().FindChildInLayoutFile( 'PopUpXrayModelOrImageReveal' ).AddClass( 'popup-xray-reverse-effect' );
		$.Schedule( 1, function()
		{
			oData.linePanel.visible = true;
			_AnimClip( oData );
		} );
	};
	var _AnimClip = function( oData )
	{
		if ( oData.clipValue <= 100 )
		{
			oData.clipPanel.style.clip = 'rect( 0%, 100%, 100%, ' + oData.clipValue + '% );';
			oData.clipValue = oData.clipValue  + 1;
			oData.linePanel.style.transform = 'translatex( -' + oData.lineValue + '%);';
			oData.lineValue = oData.lineValue - 1;
			$.Schedule( 0.02, _AnimClip.bind( undefined, oData ) );
		}
		else
		{
			oData.linePanel.AddClass( 'hide' );
			oData.clipPanel.AddClass( 'hide' );
			_SetUpPanelElements();
		}
	};
	var _AnimSquares = function( aPanels )
	{
		if ( $.GetContextPanel().FindChildInLayoutFile( 'XrayItemsPanel' ).visible )
		{
			aPanels.forEach( panel =>
			{
				panel.style.backgroundColor = 'rgba(255, 255, 255, 0.0' + Math.ceil( Math.random() * 10 ) + ');';
			} );
			$.Schedule( 1, _AnimSquares.bind( undefined, aPanels ) );
		}
	};
	var _ShowXrayReward = function()
	{
		l = null;
		if ( d )
		{
			_SetUpPanelElements();
		}
		else
		{
			_TimeoutPopup();
		}
	};
	var _UpdateXrayRewardTile = function( itemId )
	{
		var oData = ItemInfo.GetItemsInXray();
		d = itemId === oData.reward ? oData.reward : '';
		_PreCacheTextureForNewWeaponInpsect();
		_SetCaseModelImage( itemId, 'PopUpXrayModelOrImageReveal' );
	};
	var _UpdateScrollResultTile = function( numericType, type, itemId )
	{
		if ( type === "crate_unlock" ||
			type === 'graffity_unseal' ||
			type === 'xray_item_reveal' ||
			type === "xray_item_claim"
		)
		{
			if ( o )
			{
				var oData = ItemInfo.GetItemsInXray();
				if ( oData.reward && type === 'xray_item_reveal' )
				{
					_UpdateXrayRewardTile( itemId );
					return;
				}
				else if ( type === 'xray_item_claim' )
				{
					e = itemId;
					_ShowInspect();
					return;
				}
			}
			else
			{
				e = itemId;
			}
			if ( $.GetContextPanel().FindChildInLayoutFile( 'DecodableItemsScroll' ).BHasClass( 'hidden' ) )
			{
				if ( type === 'graffity_unseal' )
				{
					_ShowInspect();
				}
				return;
			}
			else
			{
				b.forEach( element =>
				{
					var elScroll = $.GetContextPanel().FindChildInLayoutFile( element );
					var elTile = elScroll.FindChildInLayoutFile( 'ItemFromContainer' );
					_UpdateScrollTile( element, elTile, itemId );
				} );
			}
		}
		else if ( type === "ticket_activated" )
		{
			e = itemId;
			_ShowInspect();
		}
	};
	var _ItemAcquired = function( ItemId )
	{
		$.DispatchEvent( "PlaySoundEffect", "rename_purchaseSuccess", "MOUSE" );
		if ( !g && h )
		{
			var matchtingKeyDefName = ItemInfo.GetItemDefinitionName( h );
			if (  ItemInfo.ItemMatchDefName( ItemId, matchtingKeyDefName ) )
			{
				g = ItemId;
				$.DispatchEvent( 'HideStoreStatusPanel' );
				_AcknowlegeMatchingKeys( matchtingKeyDefName );
				_SetUpPanelElements();
			}
		}
		else if( j )
		{
			_ClosePopUp();
			$.DispatchEvent( 'ShowAcknowledgePopup', '', ItemId );
			$.DispatchEvent( 'HideStoreStatusPanel' );
		}
	};
	var _AcknowlegeMatchingKeys = function( matchtingKeyDefName )
	{
		var bShouldAcknowledge = true;
		AcknowledgeItems.GetItemsByType( [ matchtingKeyDefName ], bShouldAcknowledge );
	};
	var _ShowUnlockAnimation = function()
	{
		var lootListCount = InventoryAPI.GetLootListItemsCount( c );
		if ( lootListCount === undefined )
		{
			if ( InventoryAPI.IsValidItemID( e ) )
			{
				_ShowInspect();
			}
			else
			{
				_SetUpCaseOpeningCountdown();
			}
			return;
		}
		if ( lootListCount <= 1 )
		{
			_SetUpCaseOpeningCountdown();
		}
		else
		{
			_SetUpCaseOpeningScroll();
		}
		_PlayContainerSound( c, 'open' );
		_PlayContainerSound( c, 'ticker' );
	};
	var _PlayContainerSound = function(caseId, soundName) {
		$.DispatchEvent( "PlaySoundEffect", "container_" + _GetContainerType(caseId) + "_" + soundName, "MOUSE" );
	};
	var _GetContainerType = function(caseId) {
		var itemDefName = ItemInfo.GetItemDefinitionName( c );
		var slot = ItemInfo.GetSlot( c );
		if(itemDefName && ( itemDefName.indexOf("spray") != -1 || itemDefName.indexOf("tournament_pass_") != -1 ) ) {
			return 'graffiti';
		} else if(itemDefName && itemDefName.indexOf("sticker") != -1) {
			return 'sticker';
		} else if(itemDefName && itemDefName.indexOf("coupon") == 0) {
			return 'music';
		} else {
			return 'weapon';
		}
	};
	var _HidePanelForLootlistItemPreview = function()
	{
		f.visible = false;
	}
	var _ClosePopUp = function()
	{
		InventoryAPI.StopItemPreviewMusic();
		if ( f.IsValid() )
		{ 
			if ( l )
			{
				$.CancelScheduled( l );
				l = null;
			}
			var elAsyncActionBarPanel = f.FindChildInLayoutFile( 'PopUpInspectAsyncBar' );
			var elPurchase = f.FindChildInLayoutFile( 'PopUpInspectPurchaseBar' );
			if ( !elAsyncActionBarPanel.BHasClass( 'hidden' ) )
			{
				InspectAsyncActionBar.OnEventToClose();
			}
			else if ( !elPurchase.BHasClass( 'hidden' ) )
			{
				InpsectPurchaseBar.ClosePopup();
			}
		}
		_UpdateOpeningCounter.CancelTimer();
	};
	return {
		Init: _Init,
		SetUpCaseOpening: _SetUpCaseOpeningScroll,
		ClosePopUp: _ClosePopUp,
		UpdateScrollResultTile: _UpdateScrollResultTile,
		ItemAcquired: _ItemAcquired,
		ShowUnlockAnimation: _ShowUnlockAnimation
	};
} )();
( function()
{
	var _m_PanelRegisteredForEvents;
	if ( !_m_PanelRegisteredForEvents )
	{
		_m_PanelRegisteredForEvents = $.RegisterForUnhandledEvent( 'PanoramaComponent_Inventory_ItemCustomizationNotification', CapabilityDecodable.UpdateScrollResultTile );
		$.RegisterForUnhandledEvent( 'PanoramaComponent_Store_PurchaseCompleted', CapabilityDecodable.ItemAcquired );
		$.RegisterForUnhandledEvent( 'StartDecodeableAnim', CapabilityDecodable.ShowUnlockAnimation );
	}
} )();