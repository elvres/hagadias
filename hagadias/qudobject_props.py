from __future__ import annotations

import math
from typing import Union, Tuple, List

from hagadias.character_codes import STAT_NAMES
from hagadias.constants import BIT_TRANS, ITEM_MOD_PROPS, FACTION_ID_TO_NAME, \
    CYBERNETICS_HARDCODED_INFIXES, CYBERNETICS_HARDCODED_POSTFIXES, HARDCODED_CHARGE_USE, \
    CHARGE_USE_REASONS
from hagadias.helpers import cp437_to_unicode, int_or_none, \
    strip_oldstyle_qud_colors, strip_newstyle_qud_colors, pos_or_neg, make_list_from_words, \
    str_or_default, int_or_default, bool_or_default
from hagadias.dicebag import DiceBag
from hagadias.qudobject import QudObject
from hagadias.svalue import sValue

# STATIC GROUPS
# Many combat properties can come from anything that inherits from either of these.
# Use For: self.active_or_inactive_character() == ACTIVE_CHAR
# ACTIVE: What is typically considered a Character, with an inventory and combat capabilities.
# INACTIVE: What would still be helpful to have combat related stats, but have things that
# for ALL_CHARS, use self.active_or_inactive_character() > 0
ACTIVE_CHAR = 1
INACTIVE_CHAR = 2
# make them different from active characters. Usually immobile and have no attributes.
BEHAVIOR_DESCRIPTION_PARTS = ['LatchesOn', 'SapChargeOnHit', 'TemperatureAdjuster', 'Toolbox',
                              'Cybernetics2BaseItem', 'FollowersGetTeleport', 'IntPropertyChanger']


class QudObjectProps(QudObject):
    """Represents a Caves of Qud game object with properties to calculate derived stats.

    Inherits from QudObject which does all the lower level work.

    Properties should return Python types where possible (lists, bools, etc.) and leave specific
    representations to a subclass."""
    # PROPERTY HELPERS
    # Helper methods to simplify the calculation of properties, further below.
    # Sorted alphabetically.

    def attribute_helper(self, attr: str) -> Union[str, None]:
        """Helper for retrieving attributes (Strength, etc.)"""
        val = None
        if self.active_or_inactive_character() == ACTIVE_CHAR:
            if getattr(self, f'stat_{attr}_sValue'):
                try:
                    level = int(self.lv)
                except ValueError:
                    # levels can be very rarely given like "18-29"
                    level = int(self.lv.split('-')[0])
                val = str(sValue(getattr(self, f'stat_{attr}_sValue'), level=level))
            elif getattr(self, f'stat_{attr}_Value'):
                val = getattr(self, f'stat_{attr}_Value')
        elif self.inherits_from('Armor'):
            val = getattr(self, f'part_Armor_{attr}')
        return val

    def attribute_boost_factor(self, attr: str) -> Union[float, None]:
        """Returns the boost factor which is applied to this stat after it's calculated."""
        if self.active_or_inactive_character() == ACTIVE_CHAR:
            boost = int_or_none(getattr(self, f'stat_{attr}_Boost'))
            if boost is not None:
                if getattr(self, f'stat_{attr}_sValue'):  # Boost only applied if there's an sValue
                    if self.role == 'Minion' and attr in STAT_NAMES:
                        boost -= 1
                    if boost > 0:
                        return 0.25 * float(boost) + 1.0
                    else:
                        return 0.20 * float(boost) + 1.0

    def attribute_helper_min_max_or_avg(self, attr: str, mode: str) -> Union[int, None]:
        """Return the minimum, maximum, or average stat value for the given stat. Specify
        one of the following modes: 'min', 'max', or 'avg'."""
        val_str = self.attribute_helper(attr)
        if val_str is not None:
            boost_factor = self.attribute_boost_factor(attr)
            dice = DiceBag(val_str)
            if boost_factor is None:
                if mode == 'min':
                    return int(dice.minimum())
                return int(dice.maximum()) if mode == 'max' else int(dice.average())
            min_val = int(math.ceil(dice.minimum() * boost_factor))
            if mode == 'min':
                return min_val
            max_val = int(math.ceil(dice.maximum() * boost_factor))
            if mode == 'max':
                return max_val
            # the game rounds up on each rolled dice value after applying a Boost. This also
            # modifies the average, so we need to calculate that average outside of the DiceBag.
            avg_val = (min_val + max_val) / 2.0
            return int(avg_val)  # truncated averages are used for character stats on the wiki

    def attribute_helper_avg(self, attr: str) -> Union[int, None]:
        """Return the average stat value for the given stat."""
        return self.attribute_helper_min_max_or_avg(attr, 'avg')

    def attribute_helper_min(self, attr: str) -> Union[int, None]:
        """Return the minimum stat value for the given stat."""
        return self.attribute_helper_min_max_or_avg(attr, 'min')

    def attribute_helper_max(self, attr: str) -> Union[int, None]:
        """Return the maximum stat value for the given stat."""
        return self.attribute_helper_min_max_or_avg(attr, 'max')

    def attribute_helper_mod(self, attr: str, statmode: str = 'avg') -> Union[int, None]:
        """Return the creature's attribute modifier for the given stat. Optionally, you may
        also specify a statmode ('min', 'max', or 'avg') to determine the modifier based on the
        creature's minimum, maximum, or average stat value. Average is used by default"""
        if statmode == 'min':
            val = self.attribute_helper_min(attr)
        elif statmode == 'max':
            val = self.attribute_helper_max(attr)
        else:
            val = self.attribute_helper_avg(attr)
        if val is not None:
            val = (val - 16) // 2  # return stat modifier for average roll
            return val

    def resistance(self, element: str) -> Union[int, None]:
        """The elemental resistance/weakness the equipment or NPC has.
        Helper function for properties."""
        val = getattr(self, f'stat_{element}Resistance_Value')
        if self.part_Armor:
            if element == "Electric":
                element = "Elec"  # short form in armor
            val = getattr(self, f'part_Armor_{element}')
        if self.part_Roboticized and self.part_Roboticized_ChanceOneIn == '1':
            if element in ['Heat', 'Cold']:
                val = 25
            elif element == 'Electric':
                val = -50
        if self.mutation:
            for mutation, info in self.mutation.items():
                if mutation == 'Carapace' and element in ['Heat', 'Cold']:
                    val = 0 if val is None else int(val)
                    val += int(info['Level']) * 5 + 5
                if mutation == 'SlogGlands' and element == 'Acid':
                    val = 100
        return int_or_none(val)

    def projectile_object(self, part_attr: str = '') -> Union[QudObjectProps, str, None]:
        """Retrieve the projectile object for a MissileWeapon or Arrow.
        If part_attr specified, retrieve the specific part attribute
        value from that projectile object instead.

        Doesn't work for bows because their projectile object varies
        depending on the type of arrow loaded into them."""
        if self.part_MissileWeapon is not None or self.is_specified('part_AmmoArrow'):
            parts = ['part_BioAmmoLoader_ProjectileObject',
                     'part_AmmoArrow_ProjectileObject',
                     'part_MagazineAmmoLoader_ProjectileObject',
                     'part_EnergyAmmoLoader_ProjectileObject',
                     'part_LiquidAmmoLoader_ProjectileObject']
            for part in parts:
                attr = getattr(self, part)
                if attr is not None and attr != '':
                    item = self.qindex[attr]
                    if part_attr:
                        return getattr(item, part_attr, None)
                    else:
                        return item
        return None

    def active_or_inactive_character(self) -> Union[int, None]:
        """0: NONE 1: ACTIVE_CHARS 2: INACTIVE_CHARS. for ALL_CHARS, do > 0 check"""
        if (self.part_Physics_Takeable == "false" or self.part_Physics_Takeable == "False") and \
           self.part_Gas is None:
            # This falls under ALL_CHARS
            if self.part_Combat is not None and self.part_Brain is not None:
                return 1  # ACTIVE_CHARS
            else:
                return 2  # INACTIVE_CHARS
        return 0

    # PROPERTIES
    # These properties are the heart of hagadias. They make it easy to access attributes
    # buried in the XML, or which require some computation or translation.
    # Properties all return None implicitly if the property is not applicable to the current item.
    @property
    def accuracy(self) -> Union[int, None]:
        """How accurate the gun is."""
        if self.part_MissileWeapon is not None:
            accuracy = self.part_MissileWeapon_WeaponAccuracy
            return 0 if accuracy is None else accuracy  # 0 is default if unspecified

    @property
    def acid(self) -> Union[int, None]:
        """The elemental resistance/weakness the equipment or NPC has."""
        return self.resistance('Acid')

    @property
    def agility(self) -> Union[str, None]:
        """The agility the mutation affects, or the agility of the creature."""
        return self.attribute_helper('Agility')

    @property
    def agilitymult(self) -> Union[float, None]:
        """The stat Bonus multiplier for intrinsic agility, if specified."""
        return self.attribute_boost_factor('Agility')

    @property
    def agilityextrinsic(self) -> Union[int, None]:
        """Extra agility for a creature from extrinsic factors, such as mutations or equipment."""
        if self.active_or_inactive_character() == ACTIVE_CHAR:
            if self.mutation:
                for mutation, info in self.mutation.items():
                    if mutation == 'HeightenedAgility':
                        return (int(info['Level']) - 1) // 2 + 2

    @property
    def ammo(self) -> Union[str, None]:
        """What type of ammo is used."""
        ammo = None
        if self.part_MagazineAmmoLoader_AmmoPart:
            ammotypes = {'AmmoSlug': 'lead slug',
                         'AmmoShotgunShell': 'shotgun shell',
                         'AmmoGrenade': 'grenade',
                         'AmmoMissile': 'missile',
                         'AmmoArrow': 'arrow',
                         'AmmoDart': 'dart',
                         }
            ammo = ammotypes.get(self.part_MagazineAmmoLoader_AmmoPart)
        elif self.part_EnergyAmmoLoader_ChargeUse and int(self.part_EnergyAmmoLoader_ChargeUse) > 0:
            if self.part_EnergyCellSocket and self.part_EnergyCellSocket_SlotType == 'EnergyCell':
                ammo = 'energy'
            elif self.part_LiquidFueledPowerPlant:
                ammo = self.part_LiquidFueledPowerPlant_Liquid
        elif self.part_LiquidAmmoLoader:
            ammo = self.part_LiquidAmmoLoader_Liquid
        return ammo

    @property
    def ammodamagetypes(self) -> Union[list, None]:
        """Damage attributes associated with the projectile.

        Example: ["Exsanguination", "Disintegrate"] for ProjectileBloodGradientHandVacuumPulse"""
        attributes = self.projectile_object('part_Projectile_Attributes')
        if attributes is not None:
            return attributes.split()

    @property
    def ammoperaction(self) -> [int, None]:
        """How much ammo this weapon uses per action. This sometimes differs from the
        shots per action."""
        return self.part_MissileWeapon_AmmoPerAction

    @property
    def animatable(self) -> [bool, None]:
        """If the thing can be animated using spray a brain or nanoneuro animator."""
        if self.tag_Animatable is not None:
            return True

    @property
    def aquatic(self) -> Union[bool, None]:
        """If the creature requires to be submerged in water."""
        if self.inherits_from('Creature'):
            if self.part_Brain_Aquatic is not None:
                return True if self.part_Brain_Aquatic == "true" else False

    @property
    def av(self) -> Union[int, None]:
        """The AV that an item provides, or the AV that a creature has."""
        av = None
        if self.part_Armor_AV:  # the AV of armor
            av = self.part_Armor_AV
        if self.part_Shield_AV:  # the AV of a shield
            av = self.part_Shield_AV
        if self.active_or_inactive_character() > 0:
            # the AV of creatures and stationary objects
            av = int(self.stat_AV_Value)  # first, creature's intrinsic AV
            applied_body_av = False
            if self.mutation:
                for mutation, info in self.mutation.items():
                    if mutation == 'Carapace':
                        av += int(info['Level']) // 2 + 3
                        applied_body_av = True
                    if mutation == 'Quills':
                        av += int(info['Level']) // 3 + 2
                        applied_body_av = True
                    if mutation == 'Horns':
                        av += (int(info['Level']) - 1) // 3 + 1
                    if mutation == 'MultiHorns':
                        av += (int(info['Level']) + 1) // 4
                    if mutation == 'SlogGlands':
                        av += 1
            if self.inventoryobject:
                # might be wearing armor
                for name in list(self.inventoryobject.keys()):
                    if name[0] in '*#@':
                        # special values like '*Junk 1'
                        continue
                    item = self.qindex[name]
                    if item.av and (not applied_body_av or item.wornon != 'Body'):
                        av += int(item.av)
        return int_or_none(av)

    @property
    def bits(self) -> Union[str, None]:
        """The bits you can get from disassembling the object.

        Example: "0034" for the spiral borer"""
        if self.part_TinkerItem and (self.part_TinkerItem_CanDisassemble != 'false' or
                                     self.part_TinkerItem_CanBuild != 'false'):
            return self.part_TinkerItem_Bits.translate(BIT_TRANS)

    @property
    def bleedliquid(self) -> Union[str, None]:
        """What liquid something bleeds. Only returns interesting liquids (not blood)"""
        robotic = self.part_Roboticized and self.part_Roboticized_ChanceOneIn == '1'
        if self.is_specified('part_BleedLiquid') or robotic:
            liquid = 'oil' if robotic else self.part_BleedLiquid.split('-')[0]
            if liquid != "blood":  # it's interesting if they don't bleed blood
                return liquid

    @property
    def bodytype(self) -> Union[str, None]:
        """Returns the BodyType tag of the creature."""
        return self.part_Body_Anatomy

    @property
    def butcheredinto(self) -> Union[str, None]:
        """What a corpse item can be butchered into."""
        return self.part_Butcherable_OnSuccess

    @property
    def canbuild(self) -> Union[bool, None]:
        """Whether or not the player can tinker up this item."""
        if self.part_TinkerItem_CanBuild == 'true':
            return True
        elif self.part_TinkerItem_CanDisassemble == 'true':
            return False  # it's interesting if an item can't be built but can be disassembled

    @property
    def candisassemble(self) -> Union[bool, None]:
        """Whether or not the player can disassemble this item."""
        if self.part_TinkerItem_CanDisassemble == 'true':
            return True
        elif self.part_TinkerItem_CanBuild == 'true':
            return False  # it's interesting if an item can't be disassembled but can be built

    @property
    def chairlevel(self) -> Union[int, None]:
        """The level of this chair, used to determine the power of the Sitting effect."""
        if self.part_Chair is not None:
            level = int_or_none(self.part_Chair_Level)
            return 0 if level is None else level

    @property
    def carrybonus(self) -> Union[int, None]:
        """The carry weight bonus."""
        return int_or_none(self.part_Armor_CarryBonus)

    @property
    def chargeperdram(self) -> Union[int, None]:
        """How much charge is available per dram (for liquid-fueled cells or machines)."""
        return int_or_none(self.part_LiquidFueledEnergyCell_ChargePerDram)

    @property
    def chargeused(self) -> Union[int, None]:
        """How much charge is used for various item functions."""
        charge = 0
        for part in self.all_attributes['part']:
            if part == 'ProgrammableRecoiler':
                continue  # parts ignored or handled elsewhere
            chg = getattr(self, f'part_{part}_ChargeUse')
            if chg is not None and int(chg) > 0:
                charge += int(chg)
        if self.name in HARDCODED_CHARGE_USE:
            charge = HARDCODED_CHARGE_USE[self.name]
        if charge > 0:
            return charge

    @property
    def chargefunction(self) -> Union[str, None]:
        """The features or functions that the charge is used for."""
        funcs = []
        detailedfuncs = []
        for part in self.all_attributes['part']:
            if part == 'ProgrammableRecoiler':
                continue  # parts ignored or handled elsewhere
            chg = getattr(self, f'part_{part}_ChargeUse')
            if chg is not None and int(chg) > 0:
                if part == 'StunOnHit':
                    func = 'Stun effect'
                elif part == 'EnergyAmmoLoader' or part == 'Gaslight':
                    func = 'Weapon Power'
                elif part == 'VibroWeapon':
                    func = 'Adaptive Penetration'
                elif part == 'MechanicalWings':
                    func = 'Flight'
                elif part == 'RocketSkates':
                    func = 'Power Skate'
                elif part == 'GeomagneticDisc':
                    func = 'Throw Effect'
                elif part == 'Teleporter':
                    func = 'Teleportation'
                elif part == 'EquipStatBoost':
                    func = 'Stat Boost'
                elif part == 'PartsGas':
                    func = 'Gas Dispersion'
                elif part == 'ReduceCooldowns':
                    func = 'Cooldown Reduction'
                elif part == 'RealityStabilization':
                    func = 'Reality Stabilization'
                elif part == 'LatchesOn':
                    func = 'Latch Effect'
                elif part == 'Toolbox':
                    func = 'Tinker Bonus'
                elif part == 'ConversationScript':
                    func = 'Audio Processing'
                elif getattr(self, f'part_{part}_NameForStatus') is not None:
                    func = getattr(self, f'part_{part}_NameForStatus')
                elif part == 'Chair':  # handle chairs without a NameForStatus
                    func = 'Chair Effect'
                else:
                    func = part  # default to part name if no other match
                if func is not None:
                    funcs.append(func)
                    detailedfuncs.append(func + ' [' + chg + ']')
        if self.name in CHARGE_USE_REASONS:
            func = CHARGE_USE_REASONS[self.name]
            funcs.append(func)
            if self.name in HARDCODED_CHARGE_USE:
                detailedfuncs.append(f'{func} [{HARDCODED_CHARGE_USE[self.name]}]')
            else:
                detailedfuncs.append(func)
        if len(funcs) == 0:
            return None
        elif len(funcs) == 1:
            return funcs[0]  # if only one function, return the simple name
        else:
            return ', '.join(detailedfuncs)  # if multiple, return names with charge amount appended

    @property
    def cold(self) -> Union[int, None]:
        """The elemental resistance/weakness the equipment or NPC has."""
        return self.resistance('Cold')

    @property
    def colorstr(self) -> Union[str, None]:
        """The Qud color code associated with the RenderString."""
        if self.part_Render_ColorString:
            return self.part_Render_ColorString
        if self.part_Gas_ColorString:
            return self.part_Gas_ColorString

    @property
    def commerce(self) -> Union[float, None]:
        """The value of the object."""
        if self.inherits_from('Item') or self.inherits_from('BaseThrownWeapon'):
            value = self.part_Commerce_Value
            if value is not None:
                return float(value)

    @property
    def complexity(self) -> Union[int, None]:
        """The complexity of the object, used for psychometry."""
        if self.part_Examiner_Complexity is None:
            val = 0
        else:
            val = int(self.part_Examiner_Complexity)
        if self.part_AddMod_Mods is not None:
            modprops = ITEM_MOD_PROPS
            for mod in self.part_AddMod_Mods.split(','):
                if mod in modprops:
                    if (modprops[mod]['ifcomplex'] is True) and (val <= 0):
                        continue  # no change because the item isn't already complex
                    val += int(modprops[mod]['complexity'])
        for key in self.part.keys():
            if key.startswith('Mod'):
                modprops = ITEM_MOD_PROPS
                if key in modprops:
                    if (modprops[key]['ifcomplex'] is True) and (val <= 0):
                        continue  # ditto
                    val += int(modprops[key]['complexity'])
        if val > 0 or self.canbuild:
            return val

    @property
    def cookeffect(self) -> Union[list, None]:
        """The possible cooking effects of an item."""
        ingred_type = self.part_PreparedCookingIngredient_type
        if ingred_type is not None:
            return ingred_type.split(',')

    @property
    def corpse(self) -> Union[str, None]:
        """What corpse a character drops."""
        if self.part_Corpse_CorpseBlueprint is not None and int(self.part_Corpse_CorpseChance) > 0 \
                and (self.part_Roboticized is None or self.part_Roboticized_ChanceOneIn != '1'):
            return self.part_Corpse_CorpseBlueprint

    @property
    def corpsechance(self) -> Union[int, None]:
        """The chance of a corpse dropping, if corpsechance is >0"""
        chance = self.part_Corpse_CorpseChance
        if chance is not None and int(chance) > 0 and \
                (self.part_Roboticized is None or self.part_Roboticized_ChanceOneIn != '1'):
            return int(chance)

    @property
    def cursed(self) -> Union[bool, None]:
        """If the item cannot be removed by normal circumstances."""
        if self.part_Cursed is not None:
            return True

    @property
    def damage(self) -> Union[str, None]:
        """The damage dealt by this object. Often a dice string."""
        val = None
        if self.inherits_from('MeleeWeapon') or self.is_specified('part_MeleeWeapon'):
            val = self.part_MeleeWeapon_BaseDamage
        if self.part_Gaslight:
            val = self.part_Gaslight_ChargedDamage
        if self.part_ThrownWeapon is not None:
            if self.is_specified('part_GeomagneticDisc'):
                val = self.part_GeomagneticDisc_Damage
            else:
                val = self.part_ThrownWeapon_Damage
                if val is None:
                    val = 1  # default damage for ThrownWeapon
        projectiledamage = self.projectile_object('part_Projectile_BaseDamage')
        if projectiledamage:
            val = projectiledamage
        return val

    @property
    def demeanor(self) -> Union[str, None]:
        """The demeanor of the creature."""
        if self.active_or_inactive_character() == ACTIVE_CHAR:
            if self.part_Brain_Calm is not None:
                return "docile" if self.part_Brain_Calm.lower() == "true" else "neutral"
            if self.part_Brain_Hostile is not None:
                return "aggressive" if self.part_Brain_Hostile.lower() == "true" else "neutral"

    @property
    def desc(self) -> Union[str, None]:
        """The short description of the object, with color codes included (ampersands escaped)."""
        desc = None
        desc_extra = []
        if self.part_Description_Short == 'A hideous specimen.':
            pass  # hide items with default description
        elif self.part_Description_Short:
            # TODO: Refactor or break into a separate file.
            # Note that the order of description rules below is meaningful - it attempts to do the
            # best job possible mimicking the order of rules on items in game. It's probably
            # not possible to perfectly represent everything unless we actually iterate over the
            # object's parts in XML order (and output associated rules in that same order)
            desc = self.part_Description_Short
            is_item = False
            if self.inherits_from("Item"):  # append resistances, attributes, and other rules text
                is_item = True
                # reputation
                if self.part_AddsRep is not None:
                    factions = self.part_AddsRep_Faction.split(',')
                    rep_value = self.part_AddsRep_Value
                    for faction in factions:
                        amt = rep_value
                        if ':' in faction:
                            vals = faction.split(':')
                            amt = vals[1]
                            faction = vals[0]
                        if amt[0] not in ['+', '-']:
                            amt = f'+{amt}'
                        if faction == '*allvisiblefactions':
                            txt = f'{amt} reputation with every faction'
                        else:
                            if faction in FACTION_ID_TO_NAME:
                                faction = FACTION_ID_TO_NAME[faction]
                            txt = f'{amt} reputation with {faction}'
                        desc_extra.append('{{rules|' + txt + '}}')
                # missile weapon rules
                if self.part_MissileWeapon is not None:
                    skill = str_or_default(self.part_MissileWeapon_Skill, 'Rifle')
                    if skill == 'Rifle':
                        skill = 'Bows & Rifles'
                    elif skill == 'HeavyWeapons':
                        skill = 'Heavy Weapon'
                    accuracy = int_or_default(self.part_MissileWeapon_WeaponAccuracy, 0)
                    accuracy_str = 'Very Low'
                    if accuracy <= 0:
                        accuracy_str = 'Very High'
                    elif accuracy < 5:
                        accuracy_str = 'High'
                    elif accuracy < 10:
                        accuracy_str = 'Medium'
                    elif accuracy < 25:
                        accuracy_str = 'Low'
                    ammoper = int_or_default(self.part_MissileWeapon_AmmoPerAction, 1)
                    shotsper = int_or_default(self.part_MissileWeapon_ShotsPerAction, 1)
                    showshots = bool_or_default(self.part_MissileWeapon_bShowShotsPerAction, True)
                    nowildfire = bool_or_default(self.part_MissileWeapon_NoWildfire, False)
                    penstat = self.part_MissileWeapon_ProjectilePenetrationStat
                    txt = '{{rules|'
                    txt += f'Weapon Class: {skill}'
                    txt += f'\nAccuracy: {accuracy_str}'
                    if ammoper > 1:
                        txt += f'\nMultiple ammo used per shot: {ammoper}'
                    if showshots and shotsper > 1:
                        txt += f'\nMultiple projectiles per shot: {shotsper}'
                    if nowildfire:
                        txt += '\nSpray fire: This item can be fired while adjacent to multiple ' \
                               + 'enemies without risk of the shot going wild.'
                    if skill == 'Heavy Weapon':
                        txt += '\n-25 move speed'
                    if penstat:
                        txt += '\nProjectiles fired with this weapon receive bonus penetration ' \
                               + f'based on the wielder\'s {penstat}.'
                    txt += '}}'
                    desc_extra.append(txt)
                # resists
                resists = []
                # attributes [positiveColor, negativeColor, isResistance]
                attrs = {'heat': ['R', 'R', True],
                         'cold': ['C', 'C', True],
                         'electrical': ['W', 'W', True],
                         'acid': ['G', 'G', True],
                         'willpower': ['C', 'R', False],
                         'ego': ['C', 'R', False],
                         'agility': ['C', 'R', False],
                         'toughness': ['C', 'R', False],
                         'strength': ['C', 'R', False],
                         'intelligence': ['C', 'R', False],
                         'quickness': ['C', 'R', False],
                         'movespeedbonus': ['C', 'R', False]}
                for attr in attrs:
                    resist = getattr(self, f'{attr}')
                    if resist:
                        if self.name == 'Stopsvaalinn' and attr == 'ego':
                            continue  # Stopsvaalinn's ego bonus is already displayed in rule text
                        if self.name == 'Cyclopean Prism':  # special handling for amaranthine prism
                            if attr == 'ego':
                                resist = '+1'
                            elif attr == 'willpower':
                                resist = '-1'
                        if str(resist)[0] not in ['+', '-']:
                            resist_str = f'{pos_or_neg(resist)}{resist}'
                        else:
                            resist_str = str(resist)
                        attr_name = attr if attr != 'movespeedbonus' else 'move speed'
                        attr_color = attrs[attr][0] if resist_str[0] != '-' else attrs[attr][1]
                        resist_str = f"{resist_str} " + attr_name.title() + \
                                     (" Resistance" if attrs[attr][2] is True else "")
                        resists.append(f"{{{{{attr_color}|{resist_str}}}}}")
                if len(resists) > 0:
                    desc_extra.append('\n'.join(resists))
                # carrybonus
                carry_bonus = self.carrybonus
                if carry_bonus:
                    if carry_bonus > 0:
                        carry_bonus = f'+{carry_bonus}'
                    desc_extra.append('{{rules|' + carry_bonus + '% carry capacity}}')
                # shields
                if self.part_Shield is not None:
                    desc_extra.append('{{rules|Shields only grant their AV when you ' +
                                      'successfully block an attack.}}')
                # compute nodes
                if self.part_ComputeNode is not None:
                    if self.part_ComputeNode_WorksOnEquipper == 'true':
                        power = self.part_ComputeNode_Power
                        power = '20' if power is None else power
                        desc_extra.append('{{rules|When equipped and powered, provides ' + power +
                                          ' units of compute power to the local lattice.}}')
                # active light source
                if self.part_ActiveLightSource is not None:
                    if self.part_ActiveLightSource_WorksOnEquipper == 'true':
                        if self.part_ActiveLightSource_ShowInShortDescription is None or \
                                self.part_ActiveLightSource_ShowInShortDescription == 'true':
                            radius = self.part_ActiveLightSource_Radius
                            radius = '5' if radius is None else radius
                            desc_extra.append('{{rules|When equipped, provides light in radius ' +
                                              radius + '.}}')
                # add item-specific rules text, if applicable
                if self.name == 'Rocket Skates':
                    rule1 = 'Replaces Sprint with Power Skate (unlimited duration).'
                    rule2 = 'Emits plumes of fire when the wearer moves while power skating.'
                    desc_extra.append('{{rules|' + rule1 + '}}')
                    desc_extra.append('{{rules|' + rule2 + '}}')
                elif self.name == 'Banner of the Holy Rhombus':
                    desc_extra.append('{{rules|Bestows the {{r|war trance}} effect to the' +
                                      ' Putus Templar who can see this item.')
                # add rules text for save modifier, if applicable
                if self.part_SaveModifier is not None:
                    if self.part_SaveModifier_ShowInShortDescription is None or \
                            self.part_SaveModifier_ShowInShortDescription == 'true':
                        amt = self.part_SaveModifier_Amount
                        amt = '1' if amt is None else amt
                        vs = self.part_SaveModifier_Vs
                        save_mod_str = f'{amt} on saves'
                        if vs is not None and vs != '':
                            save_mod_str += f' vs. {make_list_from_words(vs.split(","))}'
                        desc_extra.append('{{rules|' + save_mod_str + '.}}')
            if self.part_Roboticized and self.part_Roboticized_ChanceOneIn == '1':
                desc_postfix = 'There is a low, persistent hum emanating outward.' \
                    if not self.part_Roboticized_DescriptionPostfix \
                    else self.part_Roboticized_DescriptionPostfix
                desc += f' {desc_postfix}'
            if self.part_PartsGas is not None:
                chance = self.part_PartsGas_Chance
                if chance is not None:
                    rule = f'{chance}% chance per turn to repel gases near its'
                else:
                    rule = 'Repels gases near its'
                if is_item:
                    rule += ' wielder or wearer.' if self.name == 'Wrist Fan' else ' user.'
                else:
                    rule += 'elf.'
                desc_extra.append('{{rules|' + rule + '}}')
            if self.intproperty_GenotypeBasedDescription:
                desc_extra.append(f"[True kin]\n{self.property_TrueManDescription_Value}")
                desc_extra.append(f"[Mutant]\n{self.property_MutantDescription_Value}")
            # cybernetics infixes
            cybernetic_rules = '{{rules|'
            for part in CYBERNETICS_HARDCODED_INFIXES:
                if self.is_specified(f'part_{part}'):
                    cybernetic_rules += f'{CYBERNETICS_HARDCODED_INFIXES[part]}\n\n'
                    break
            # BehaviorDescriptions (predominantly cybernetics, but also includes some other items)
            for part in BEHAVIOR_DESCRIPTION_PARTS:
                if self.is_specified(f'part_{part}'):
                    behavior_desc = getattr(self, f'part_{part}_BehaviorDescription')
                    if behavior_desc is not None and behavior_desc != '':
                        cybernetic_rules += behavior_desc
            # additional cybernetics postfixes
            if self.part_Cybernetics2BaseItem_Slots is not None:
                body_parts = self.part_Cybernetics2BaseItem_Slots
                body_parts = body_parts.replace(',', ', ')
                cost = self.part_Cybernetics2BaseItem_Cost
                if len(desc_extra) > 0 or len(cybernetic_rules) > len('{{rules|'):
                    cybernetic_rules += '\n\n'
                txt = ''
                if self.tag_CyberneticsDestroyOnRemoval is not None:
                    txt += 'Destroyed when uninstalled.\n'
                txt += f'Target body parts: {body_parts}\n'
                txt += f'License points: {cost}\n'
                txt += 'Only compatible with True Kin genotypes'
                for part in CYBERNETICS_HARDCODED_POSTFIXES:
                    if self.is_specified(f'part_{part}'):
                        txt += f'\n{CYBERNETICS_HARDCODED_POSTFIXES[part]}'
                        break
                cybernetic_rules += txt + '}}'
            # append rules if we found any
            if len(cybernetic_rules) > len('{{rules|'):
                desc_extra.append(cybernetic_rules)
            if self.part_RulesDescription:
                if self.part_RulesDescription_AltForGenotype == "True Kin":
                    desc_extra.append(f"[Mutant]\n{{{{rules|{self.part_RulesDescription_Text}}}}}")
                    desc_extra.append("[True Kin]\n{{rules|" +
                                      self.part_RulesDescription_GenotypeAlt + "}}")
                else:
                    desc_extra.append(f"{{{{rules|{self.part_RulesDescription_Text}}}}}")
            if self.part_AddsTelepathyOnEquip is not None:
                desc_extra.insert(0, "{{rules|Grants you Telepathy.}}")
            if self.part_ReduceEnergyCosts and \
                    (self.part_ReduceEnergyCosts_GenerateShortDescription is None or
                     self.part_ReduceEnergyCosts_GenerateShortDescription == 'true'):
                num = int(self.part_ReduceEnergyCosts_PercentageReduction)
                pre = '' if (int(self.part_ReduceEnergyCosts_ChargeUse) == 0) else 'when powered, '
                temp = f"{pre}provides {num}% reduction in " \
                       f"{self.part_ReduceEnergyCosts_ScopeDescription}."
                desc_extra.append("{{rules|" + temp[0].upper() + temp[1:] + "}}")
            if self.part_Description_Mark:
                desc_extra.append(self.part_Description_Mark)
            if self.part_BonusPostfix is not None:
                desc_extra.append(self.part_BonusPostfix_Postfix)
        if desc is not None:
            if len(desc_extra) > 0:
                desc += '\n\n' + '\n'.join(desc_extra)
            desc = desc.replace('\r\n', '\n')  # currently, only the description for Bear
            desc = desc.replace('~J211', '')
        return desc

    @property
    def destroyonunequip(self) -> Union[bool, None]:
        """If the object is destroyed on unequip."""
        if self.part_DestroyOnUnequip is not None:
            return True

    @property
    def displayname(self) -> Union[str, None]:
        """The display name of the object, with color codes removed. Used in UI and wiki."""
        dname = ""
        if self.part_Render_DisplayName is not None:
            dname = self.part_Render_DisplayName
            dname = strip_oldstyle_qud_colors(dname)
            dname = strip_newstyle_qud_colors(dname)
        return dname

    @property
    def dramsperuse(self) -> Union[int, None]:
        """The number of drams of liquid consumed by each shot action."""
        if self.is_specified('part_LiquidAmmoLoader'):
            return 1  # LiquidAmmoLoader always uses 1 dram per action
        # TODO: calculate fractional value for blood-gradient hand vacuum

    @property
    def dv(self) -> Union[int, None]:
        """The Dodge Value of this object."""
        dv = None
        if self.part_Armor_DV is not None:  # the DV of armor
            dv = int(self.part_Armor_DV)
        if self.part_Shield_DV is not None:  # the DV of a shield
            dv = int(self.part_Shield_DV)
        elif (char_type := self.active_or_inactive_character()) == INACTIVE_CHAR:
            dv = -10
        elif char_type == ACTIVE_CHAR:
            # the 'DV' here is the actual DV of the creature or NPC, after:
            # base of 6 plus any explicit DV bonus,
            # skills, agility modifier (which may be a range determined by
            # dice rolls, and which changes DV by 1 for every 2 points of agility
            # over/under 16), and any equipment that is guaranteed to be worn
            if self.is_specified('part_Brain_Mobile') and (self.part_Brain_Mobile == 'false' or
                                                           self.part_Brain_Mobile == 'False'):
                dv = -10
            else:
                dv = 6
                if self.stat_DV_Value is not None:
                    dv += int(self.stat_DV_Value)
                if self.skill_Acrobatics_Dodge:  # the 'Spry' skill
                    dv += 2
                if self.skill_Acrobatics_Tumble:  # the 'Tumble' skill
                    dv += 1
                dv += self.attribute_helper_mod('Agility')
                applied_body_dv = False
                # does this creature have mutations that affect DV?
                if self.mutation:
                    for mutation, info in self.mutation.items():
                        if mutation == 'Carapace':
                            dv -= 2
                            applied_body_dv = True
                # does this creature have armor with DV modifiers to add?
                if self.inventoryobject:
                    for name in list(self.inventoryobject.keys()):
                        if name[0] in '*#@':
                            # special values like '*Junk 1'
                            continue
                        item = self.qindex[name]
                        if item.dv and (not applied_body_dv or item.wornon != 'Body'):
                            dv += item.dv
        return int_or_none(dv)

    @property
    def dynamictable(self) -> Union[list, None]:
        """What dynamic tables the object is a member of.

        Returns a list of strings, the dynamic tables."""
        if self.tag_ExcludeFromDynamicEncounters is not None:
            return None
        tables = []
        for key, val in self.tag.items():
            if key.startswith('DynamicObjectsTable'):
                if 'Value' in val and val['Value'] == '{{{remove}}}':
                    continue  # explicitly disallowed from an inherited dynamic table
                tables.append(key.split(':')[1])
        return tables if len(tables) > 0 else None

    @property
    def eatdesc(self) -> Union[str, None]:
        """The text when you eat this item."""
        return self.part_Food_Message

    @property
    def ego(self) -> Union[str, None]:
        """The creature's ego stat or the ego bonus supplied by a piece of equipment."""
        if self.name == 'Stopsvaalinn':
            return "1"
        val = self.attribute_helper('Ego')
        return f"{val}+3d1" if self.name == "Wraith-Knight Templar" else val

    @property
    def egomult(self) -> Union[float, None]:
        """The stat Bonus multiplier for intrinsic ego, if specified."""
        return self.attribute_boost_factor('Ego')

    @property
    def egoextrinsic(self) -> Union[int, None]:
        """Extra ego for a creature from extrinsic factors, such as mutations or equipment."""
        if self.active_or_inactive_character() == ACTIVE_CHAR:
            if self.mutation and 'Beak' in self.mutation.keys():
                return 1

    @property
    def electric(self) -> Union[int, None]:
        """The elemental resistance/weakness the equipment or NPC has."""
        return self.resistance('Electric')

    @property
    def electrical(self) -> Union[int, None]:
        """The elemental resistance/weakness the equipment or NPC has.
        *egocarib 10/4/2020 - I am pretty sure this property is unused, but leaving it here
         just in case. Most things use 'electric' since that is our wiki template field name"""
        return self.resistance('Electric')

    @property
    def elementaldamage(self) -> Union[str, None]:
        """The elemental damage dealt, if any, as a range."""
        if self.is_specified('part_ModFlaming'):
            tierstr = self.part_ModFlaming_Tier
            elestr = str(int(int(tierstr) * 0.8)) + '-' + str(int(int(tierstr) * 1.2))
        elif self.is_specified('part_ModFreezing'):
            tierstr = self.part_ModFreezing_Tier
            elestr = str(int(int(tierstr) * 0.8)) + '-' + str(int(int(tierstr) * 1.2))
        elif self.is_specified('part_ModElectrified'):
            tierstr = self.part_ModElectrified_Tier
            elestr = str(int(tierstr)) + '-' + str(int(int(tierstr) * 1.5))
        else:
            elestr = self.part_MeleeWeapon_ElementalDamage
        return elestr

    @property
    def elementaltype(self) -> Union[str, None]:
        """For elemental damage dealt, what the type of that damage is."""
        if self.is_specified('part_ModFlaming'):
            elestr = 'Fire'
        elif self.is_specified('part_ModFreezing'):
            elestr = 'Cold'
        elif self.is_specified('part_ModElectrified'):
            elestr = 'Electric'
        else:
            elestr = self.part_MeleeWeapon_Element
        return elestr

    @property
    def empsensitive(self) -> Union[bool, None]:
        """Returns yes if the object is empensitive. Can be found in multiple parts."""
        parts = ['EquipStatBoost',
                 'BootSequence',
                 'NavigationBonus',
                 'SaveModifier',
                 'LiquidFueledPowerPlant',
                 'LiquidProducer',
                 'TemperatureAdjuster'
                 ]
        if any(getattr(self, f'part_{part}_IsEMPSensitive') == 'true' for part in parts):
            return True
        parts = ['EnergyCellSocket',
                 'ZeroPointEnergyCollector',
                 'ModFlaming',
                 'ModFreezing',
                 'ModElectrified']
        if any(getattr(self, f'part_{part}') is not None for part in parts):
            return True

    @property
    def energycellrequired(self) -> Union[bool, None]:
        """Returns True if the object requires an energy cell to function."""
        if self.is_specified('part_EnergyCellSocket'):
            return True

    @property
    def exoticfood(self) -> Union[bool, None]:
        """When preserved, whether the player must explicitly agree to preserve it."""
        if self.tag_ChooseToPreserve is not None:
            return True

    @property
    def faction(self) -> Union[list, None]:
        """The factions this creature has loyalty to.

        Returned as a list of tuples of faction, value like
        [('Joppa', 100), ('Barathrumites', 100)]

        Example XML source:
        <part Name="Brain" Wanders="false" Factions="Joppa-100,Barathrumites-100" />
        """
        ret = None
        if self.part_Brain_Factions:
            ret = []
            for part in self.part_Brain_Factions.split(','):
                if '-' in part:
                    # has format like `Joppa-100,Barathrumites-100`
                    faction, value = part.split('-')
                    ret.append((faction, int(value)))
                else:
                    print(f'FIXME: unexpected faction format: {part} in {self.name}')
        return ret

    @property
    def flametemperature(self) -> Union[int, None]:
        """The temperature that this object sets on fire. Only for items."""
        if self.inherits_from('Item') and self.is_specified('part_Physics'):
            return int_or_none(self.part_Physics_FlameTemperature)

    @property
    def flyover(self) -> Union[bool, None]:
        """Whether a flying creature can pass over this object."""
        if self.inherits_from('Wall') or self.inherits_from('Furniture'):
            if self.tag_Flyover is not None:
                return True
            else:
                return False

    @property
    def gasemitted(self) -> Union[str, None]:
        """The gas emitted by the weapon (typically missile weapon 'pumps')."""
        return self.projectile_object('part_GasOnHit_Blueprint')

    @property
    def gender(self) -> Union[str, None]:
        """The gender of the object."""
        if ((self.tag_Gender_Value is not None or
             (self.tag_RandomGender_Value is not None and ',' not in self.tag_RandomGender_Value))
                and self.active_or_inactive_character() == ACTIVE_CHAR):
            gender = self.tag_Gender_Value
            if gender is None:
                gender = self.tag_RandomGender_Value
            return gender

    @property
    def harvestedinto(self) -> Union[str, None]:
        """What an item produces when harvested."""
        return self.part_Harvestable_OnSuccess

    @property
    def hasmentalshield(self) -> Union[bool, None]:
        """If a creature has a mental shield."""
        if self.active_or_inactive_character() == ACTIVE_CHAR:
            if self.part_MentalShield is not None or "Mechanical" in self.name or \
               (self.part_Roboticized and self.part_Roboticized_ChanceOneIn == '1'):
                return True

    @property
    def healing(self) -> Union[str, None]:
        """How much a food item heals when used.

        Example: "1d16+24" for Witchwood Bark"""
        return self.part_Food_Healing

    @property
    def heat(self) -> Union[int, None]:
        """The elemental resistance/weakness the equipment or NPC has."""
        return self.resistance('Heat')

    @property
    def hidden(self) -> Union[int, None]:
        """If hidden, what difficulty is required to find them.

        Example: 15 for Yonderbrush"""
        return int_or_none(self.part_Hidden_Difficulty)

    @property
    def hp(self) -> Union[str, None]:
        """The hitpoints of a creature or object.

        Returned as a string because some hitpoints are given as sValues, which can be
        strings, although they currently are not using this feature."""
        if self.active_or_inactive_character() > 0:
            if self.stat_Hitpoints_sValue is not None:
                return self.stat_Hitpoints_sValue
            elif self.stat_Hitpoints_Value is not None:
                return self.stat_Hitpoints_Value

    @property
    def hunger(self) -> Union[str, None]:
        """How much hunger it satiates.

        Example: "Snack" for Vanta Petals"""
        return self.part_Food_Satiation

    @property
    def hurtbydefoliant(self) -> Union[int, None]:
        """If the thing is hurt by defoliant.
        0/None = no damage
        1 = normal damage
        2 = significant damage"""
        if self.tag_LivePlant is not None:
            if self.part_Combat is not None and self.tag_GasDamageAsIfInanimate is None:
                return 1
            else:
                return 2

    @property
    def hurtbyfungicide(self) -> Union[int, None]:
        """If the thing is hurt by fungicide.
        0/None = no damage
        1 = normal damage
        2 = significant damage"""
        if self.tag_LiveFungus is not None:
            if self.part_Combat is not None and self.tag_GasDamageAsIfInanimate is None:
                return 1
            else:
                return 2

    @property
    def id(self) -> str:
        """The name of the object in ObjectBlueprints.xml. Should always exist."""
        return self.name

    @property
    def illoneat(self) -> Union[bool, None]:
        """If eating this makes you sick."""
        if not self.inherits_from('Corpse'):
            if self.part_Food_IllOnEat == 'true':
                return True

    @property
    def imprintchargecost(self) -> Union[int, None]:
        """How much charge is used to imprint a programmable recoiler."""
        if self.part_ProgrammableRecoiler is not None:
            charge = self.part_ProgrammableRecoiler_ChargeUse
            if charge is not None:
                return int_or_none(charge)
            return 10000  # default IProgrammableRecoiler charge use

    @property
    def inheritingfrom(self) -> Union[str, None]:
        """The ID of the parent object in the Qud object hierarchy.

        Only the root object ("Object") should return None for this."""
        return self.parent.name

    @property
    def intelligence(self) -> Union[str, None]:
        """The intelligence the mutation affects, or the intelligence of the creature."""
        return self.attribute_helper('Intelligence')

    @property
    def intelligencemult(self) -> Union[float, None]:
        """The stat Bonus multiplier for intrinsic intelligence, if specified."""
        return self.attribute_boost_factor('Intelligence')

    @property
    def intelligenceextrinsic(self) -> Union[int, None]:
        """Extra INT for a creature from extrinsic factors, such as mutations or equipment."""
        return None  # nothing currently supported here

    @property
    def inventory(self) -> List[Tuple[str, str, str, str]]:
        """The inventory of a character.

        Returns a list of tuples of strings: (name, count, equipped, chance)."""
        inv = self.inventoryobject
        if inv is not None:
            ret = []
            for name in inv:
                if name[0] in '*#@':  # Ignores stuff like '*Junk 1'
                    continue
                count = inv[name].get('Number', '1')
                equipped = 'no'  # not yet implemented
                chance = inv[name].get('Chance', '100')
                ret.append((name, count, equipped, chance))
            return ret

    @property
    def iscurrency(self) -> Union[bool, None]:
        """If the item is considered currency (price remains fixed while trading)."""
        if self.intproperty_Currency_Value == '1':
            return True

    @property
    def isfungus(self) -> Union[bool, None]:
        """If the food item contains fungus."""
        if self.tag_Mushroom is not None:
            return True

    @property
    def ismeat(self) -> Union[bool, None]:
        """If the food item contains meat."""
        if self.tag_Meat is not None:
            return True

    @property
    def ismissile(self) -> Union[bool, None]:
        """If this item is a missile weapon"""
        if self.inherits_from('MissileWeapon'):
            return True
        if self.is_specified('part_MissileWeapon'):
            return True

    @property
    def isthrown(self) -> Union[bool, None]:
        """If this item is a thrown weapon"""
        if self.part_ThrownWeapon is not None:
            return True

    @property
    def isoccluding(self) -> Union[bool, None]:
        if self.part_Render_Occluding is not None:
            if self.part_Render_Occluding == 'true' or self.part_Render_Occluding == 'True':
                return True

    @property
    def isplant(self) -> Union[bool, None]:
        """If the food item contains plants."""
        if self.tag_Plant is not None:
            return True

    @property
    def isswarmer(self) -> Union[bool, None]:
        """Whether a creature is a Swarmer."""
        if self.inherits_from('Creature'):
            if self.is_specified('part_Swarmer'):
                return True

    @property
    def leakswhenbroken(self) -> Union[str, None]:
        """If this object leaks liquid when broken, the dice string for % amount per turn leaked."""
        if self.part_LeakWhenBroken is not None:
            amt = self.part_LeakWhenBroken_PercentPerTurn
            amt = '10-20' if amt is None else amt  # 10-20% is default
            return amt

    @property
    def lightprojectile(self) -> Union[bool, None]:
        """If the gun fires light projectiles (heat immune creatures will not take damage)."""
        if self.tag_Light is not None:
            return True

    @property
    def lightradius(self) -> Union[int, None]:
        """Radius of light the object gives off."""
        val = int_or_none(self.part_LightSource_Radius)
        if val is None:
            val = int_or_none(self.part_ActiveLightSource_Radius)
        return val

    @property
    def liquidgen(self) -> Union[int, None]:
        """For liquid generators. how many turns it takes for 1 dram to generate."""
        # TODO: is this correct?
        return int_or_none(self.part_LiquidProducer_Rate)

    @property
    def liquidtype(self) -> Union[str, None]:
        """For liquid generators, the type of liquid generated."""
        return self.part_LiquidProducer_Liquid

    @property
    def liquidburst(self) -> Union[str, None]:
        """If it explodes into liquid, what kind?"""
        return self.part_LiquidBurst_Liquid

    @property
    def lv(self) -> Union[str, None]:
        """The object's level.

        Returned as a string because it may be possible for it to be an sValue, as in
        Barathrumite_FactionMemberMale which has a level sValue of "18-29"."""
        level = self.stat_Level_sValue
        if level is None:
            level = self.stat_Level_Value
        return level

    @property
    def ma(self) -> Union[int, None]:
        """The object's mental armor. For creatures, this is an averaged value."""
        if self.hasmentalshield:
            # things like Robots, Water, Stairs, etc. are not subject to mental effects.
            return None
        elif (char_type := self.active_or_inactive_character()) == INACTIVE_CHAR:
            return 0
        elif char_type == ACTIVE_CHAR:
            # MA starts at base 4
            ma = 4
            # Add MA stat value if specified
            if self.stat_MA_Value:
                ma += int(self.stat_MA_Value)
            # add willpower modifier to MA
            ma += self.attribute_helper_mod('Willpower')
            return ma

    @property
    def marange(self) -> Union[str, None]:
        """The creature's full range of potential MA values"""
        if self.hasmentalshield:
            # things like Robots, Water, Stairs, etc. are not subject to mental effects.
            return None
        elif (char_type := self.active_or_inactive_character()) == INACTIVE_CHAR:
            return None
        elif char_type == ACTIVE_CHAR:
            ma = 4
            if self.stat_MA_Value:
                ma += int(self.stat_MA_Value)
            # add willpower modifier to MA
            minmod = self.attribute_helper_mod('Willpower', 'min')
            maxmod = self.attribute_helper_mod('Willpower', 'max')
            if minmod == maxmod:
                return str(ma+minmod)
            # returning this in a bit of a weird format so that our wiki dice parser can
            # parse it correctly (it doesn't do well with ranges like -2--1 [fire ant], so we
            # would output this instead as -3+1d2
            return f'{ma+minmod-1}+1d{maxmod-minmod+1}'

    @property
    def maxammo(self) -> Union[int, None]:
        """How much ammo a gun can have loaded at once."""
        return int_or_none(self.part_MagazineAmmoLoader_MaxAmmo)

    @property
    def maxcharge(self) -> Union[int, None]:
        """How much charge it can hold (usually reserved for cells)."""
        return int_or_none(self.part_EnergyCell_MaxCharge)

    @property
    def maxvol(self) -> Union[int, None]:
        """The maximum liquid volume."""
        return int_or_none(self.part_LiquidVolume_MaxVolume)

    @property
    def maxpv(self) -> Union[int, None]:
        """The max strength bonus + our base PV."""
        pv = self.pv
        if pv is not None:
            if self.inherits_from('MeleeWeapon') or self.is_specified('part_MeleeWeapon'):
                if self.part_MeleeWeapon_MaxStrengthBonus is not None:
                    pv += int(self.part_MeleeWeapon_MaxStrengthBonus)
        return pv

    @property
    def metal(self) -> Union[bool, None]:
        """Whether the object is made out of metal."""
        if self.part_Metal is not None or \
                (self.part_Roboticized and self.part_Roboticized_ChanceOneIn == '1'):
            return True

    @property
    def modcount(self) -> Union[int, None]:
        """The number of mods on the item, if applicable.

        Example: Svensword with
            <part Name="AddMod" Mods="ModCounterweighted,ModElectrified" Tiers="5,7" />
        will return 2.

        MasterworkCarbine with
            <part Name="ModScoped" />
            <part Name="ModMasterwork" />
        will likewise return 2.
        """
        val = 0
        if self.part_AddMod_Mods is not None:
            val += len(self.part_AddMod_Mods.split(","))
        for key in self.part.keys():
            if key.startswith('Mod'):
                val += 1
        return val if val > 0 else None

    @property
    def mods(self) -> Union[List[Tuple[str, int]], None]:
        """Mods that are attached to the current item.

        Returns a list of tuples like [(modid, tier), ...].
        """
        mods = []
        if self.part_AddMod_Mods is not None:
            names = self.part_AddMod_Mods.split(',')
            if self.part_AddMod_Tiers is not None:
                tiers = self.part_AddMod_Tiers.split(',')
                tiers = [int(tier) for tier in tiers]
            else:
                tiers = [1] * len(names)
            mods.extend(zip(names, tiers))
        for key in self.part.keys():
            if key.startswith('Mod'):
                if 'Tier' in self.part[key]:
                    mods.append((key, int(self.part[key]['Tier'])))
                else:
                    mods.append((key, 1))
        return mods if len(mods) > 0 else None

    @property
    def movespeed(self) -> Union[int, None]:
        """The movespeed of a creature."""
        if self.inherits_from('Creature'):
            ms = int_or_none(self.stat_MoveSpeed_Value)
            if ms is not None:
                # https://bitbucket.org/bbucklew/cavesofqud-public-issue-tracker/issues/2634
                ms = 200 - ms
                return ms

    @property
    def movespeedbonus(self) -> Union[int, None]:
        """The movespeed bonus of an item."""
        if self.inherits_from('Item'):
            bonus = self.part_MoveCostMultiplier_Amount
            if bonus is not None:
                return -int(bonus)

    @property
    def mutatedplant(self) -> Union[bool, None]:
        """Whether this object is a MutatedPlant"""
        if self.inherits_from('MutatedPlant'):
            return True

    @property
    def mutations(self) -> Union[List[Tuple[str, int]], None]:
        """The mutations the creature has along with their level.

        Returns a list of tuples like [(name, level), ...].
        """
        mutations = []
        if self.mutation is not None:  # direct reference to <mutation> XML tag - not a property
            for mutation, data in self.mutation.items():
                postfix = f"{data['GasObject']}" if 'GasObject' in data else ''
                level = int(data['Level']) if 'Level' in data else 0
                mutations.append((mutation + postfix, level))
        if self.part_Roboticized and self.part_Roboticized_ChanceOneIn == '1':
            # additional mutations added to roboticized things
            if self.mutation is None or \
                    not any(mu in ['NightVision', 'DarkVision'] for mu in self.mutation.keys()):
                mutations.append(('DarkVision', 12))
        if len(mutations) > 0:
            return mutations

    @property
    def noprone(self) -> Union[bool, None]:
        """Returns true if has part NoKnockdown."""
        if self.part_NoKnockdown is not None:
            return True

    @property
    def omniphaseprojectile(self) -> Union[bool, None]:
        projectile = self.projectile_object()
        if projectile.is_specified('part_OmniphaseProjectile') or \
                projectile.is_specified('tag_Omniphase'):
            return True

    @property
    def oneat(self) -> Union[List[str], None]:
        """Effects granted when the object is eaten.

        Returns a list of strings, which are the effects.
        Example:
            Transform <part Name="BreatheOnEat" Class="FireBreather" Level="5"></part>
            into ['BreatheOnEatFireBreather5']"""
        effects = []
        for key, val in self.part.items():
            if key.endswith('OnEat'):
                effect = key
                if 'Class' in val:
                    effect += val['Class']
                    effect += val['Level']
                effects.append(effect)
        return effects if len(effects) > 0 else None

    @property
    def penetratingammo(self) -> Union[bool, None]:
        """If the missile weapon's projectiles pierce through targets."""
        if self.projectile_object('part_Projectile_PenetrateCreatures') is not None:
            return True

    @property
    def pettable(self) -> Union[bool, None]:
        """If the creature is pettable."""
        if self.part_Pettable is not None:
            return True

    @property
    def phase(self) -> Union[str, None]:
        """What phase the object/creature is in, if not in phase."""
        if self.part_HologramMaterial or self.tag_Omniphase:
            return "omniphase"
        if self.tag_Nullphase:
            return "nullphase"
        if self.tag_Astral:
            return "out of phase"
        if self.mutation:
            for mutation, data in self.mutation.items():
                if mutation == "Spinnerets":
                    if f"{data['Phase']}" == 'True':
                        return "out of phase"

    @property
    def poisononhit(self) -> Union[str, None]:
        if self.part_PoisonOnHit:
            pct = self.part_PoisonOnHit_Chance
            save = self.part_PoisonOnHit_Strength
            dmg = self.part_PoisonOnHit_DamageIncrement
            duration = self.part_PoisonOnHit_Duration
            pct = pct if pct is not None else '100'
            save = save if save is not None else '15'
            dmg = dmg if dmg is not None else '3d3'
            duration = duration if duration is not None else '6-9'
            return f'{pct}% to poison on hit, toughness save {save}.' + \
                   f' {dmg} damage for {duration} turns.'

    @property
    def preservedinto(self) -> Union[str, None]:
        """When preserved, what a preservable item produces."""
        return self.part_PreservableItem_Result

    @property
    def preservedquantity(self) -> Union[int, None]:
        """When preserved, how many preserves a preservable item produces."""
        return int_or_none(self.part_PreservableItem_Number)

    @property
    def pronouns(self) -> Union[str, None]:
        """Return the pronounset of a creature, if [they] have any."""
        if self.tag_PronounSet_Value is not None and self.inherits_from('Creature'):
            return self.tag_PronounSet_Value

    @property
    def pv(self) -> Union[int, None]:
        """The base PV, which is by default 4 if not set. Optional.
        The game adds 4 to internal PV values for display purposes, so we also do that here."""
        pv = None
        if self.inherits_from('MeleeWeapon') or self.is_specified('part_MeleeWeapon'):
            pv = 4
            if self.part_Gaslight_ChargedPenetrationBonus is not None:
                pv += int(self.part_Gaslight_ChargedPenetrationBonus)
            elif self.part_MeleeWeapon_PenBonus is not None:
                pv += int(self.part_MeleeWeapon_PenBonus)
        missilepv = self.projectile_object('part_Projectile_BasePenetration')
        if missilepv is not None:
            pv = int(missilepv) + 4
        if self.part_ThrownWeapon is not None:
            pv = int_or_none(self.part_ThrownWeapon_Penetration)
            if pv is None:
                pv = 1
            pv = pv + 4
        if pv is not None:
            return pv

    @property
    def pvpowered(self) -> Union[bool, None]:
        """Whether the object's PV changes when it is powered."""
        is_vibro = self.vibro and self.vibro is not None
        if is_vibro and self.is_specified('part_MissileWeapon'):
            return None
        if is_vibro and (not self.part_VibroWeapon or int(self.part_VibroWeapon_ChargeUse) > 0):
            return True
        if self.part_Gaslight and int(self.part_Gaslight_ChargeUse) > 0:
            return True
        if self.part_Projectile_Attributes == "Vorpal":
            # FIXME: this seems like it won't actually works [use projectile_object() instead?]
            return True

    @property
    def quickness(self) -> Union[int, None]:
        """Return quickness of a creature"""
        if self.active_or_inactive_character() == ACTIVE_CHAR:
            mutation_val = 0
            if self.mutation:
                for mutation, info in self.mutation.items():
                    if mutation == 'ColdBlooded':
                        mutation_val -= 10
                    if mutation == 'HeightenedSpeed':
                        mutation_val += int(info['Level']) * 2 + 13
            if mutation_val != 0:
                return mutation_val + \
                       100 if self.stat_Speed_Value is None else int(self.stat_Speed_Value)
            return int_or_none(self.stat_Speed_Value)
        if self.part_Armor:
            return int_or_none(self.part_Armor_SpeedBonus)

    @property
    def realitydistortionbased(self) -> Union[bool, None]:
        projectile = self.projectile_object()
        if projectile is not None:
            projectile_rd_info = projectile.part_TreatAsSolid_RealityDistortionBased
            if projectile_rd_info is not None and projectile_rd_info == 'true':
                return True
            projectile_vamp_rd_info = projectile.part_VampiricWeapon_RealityDistortionBased
            if projectile_vamp_rd_info is not None and projectile_vamp_rd_info == 'true':
                return True
        if self.part_MechanicalWings_IsRealityDistortionBased is not None:
            if self.part_MechanicalWings_IsRealityDistortionBased == 'true':
                return True
        if self.part_DeploymentGrenade_UsabilityEvent is not None:
            if self.part_DeploymentGrenade_UsabilityEvent == 'CheckRealityDistortionUsability':
                return True
        if self.part_Displacer is not None:
            return True
        if self.part_SpaceTimeVortex is not None:
            return True
        if self.part_EngulfingClones is not None:
            return True
        if self.part_GreaterVoider is not None:
            return True

    @property
    def reflect(self) -> Union[int, None]:
        """If it reflects, what percentage of damage is reflected."""
        return int_or_none(self.part_ModGlassArmor_Tier)

    @property
    def renderstr(self) -> Union[str, None]:
        """The character used to render this object in ASCII mode."""
        render = None
        if self.part_Render_RenderString and len(self.part_Render_RenderString) > 1:
            # some RenderStrings are given as CP437 character codes in base 10
            render = cp437_to_unicode(int(self.part_Render_RenderString))
        elif self.part_Gas is not None:
            render = '▓'
        elif self.part_Render_RenderString is not None:
            render = self.part_Render_RenderString
        return render

    @property
    def reputationbonus(self) -> Union[List[Tuple[str, int]], None]:
        """Reputation bonuses granted by the object.

        Returns a list of tuples like [(faction, value), ...].
        """
        # Examples of XML source formats:
        # <part Name="AddsRep" Faction="Apes" Value="-100" />
        # <part Name="AddsRep" Faction="Antelopes,Goatfolk" Value="100" />
        # <part Name="AddsRep" Faction="Fungi:200,Consortium:-200" />
        if self.part_AddsRep:
            reps = []
            for part in self.part_AddsRep_Faction.split(','):
                if ':' in part:
                    # has format like `Fungi:200,Consortium:-200`
                    faction, value = part.split(':')
                else:
                    # has format like `Antelopes,Goatfolk` and Value `100`
                    # or is a single faction, like `Apes` and Value `-100`
                    faction = part
                    value = self.part_AddsRep_Value
                value = int(value)
                reps.append((faction, value))
            return reps

    @property
    def role(self) -> Union[str, None]:
        """What role a creature or object has assigned.

        Example: Programmable Recoiler has "Uncommon"
        Albino ape has "Brute"
        """
        return self.property_Role_Value

    @property
    def savemodifier(self) -> Union[str, None]:
        """Returns save modifier type"""
        return self.part_SaveModifier_Vs

    @property
    def savemodifieramt(self) -> Union[int, None]:
        """returns amount of the save modifer."""
        if self.part_SaveModifier_Vs is not None:
            return int_or_none(self.part_SaveModifier_Amount)

    @property
    def seeping(self) -> Union[str, None]:
        if self.part_Gas is not None:
            if self.is_specified('part_Gas_Seeping'):
                if self.part_Gas_Seeping == 'true':
                    return 'yes'
            if self.is_specified('tag_GasGenerationAddSeeping'):
                if self.tag_GasGenerationAddSeeping_Value == 'true':
                    return 'yes'
            return 'no'

    @property
    def shotcooldown(self) -> Union[str, None]:
        """Cooldown before weapon can be fired again, typically a dice string."""
        return self.part_CooldownAmmoLoader_Cooldown

    @property
    def shots(self) -> Union[int, None]:
        """How many shots are fired in one round."""
        return int_or_none(self.part_MissileWeapon_ShotsPerAction)

    @property
    def skills(self) -> Union[str, None]:
        """The skills that certain creatures have."""
        if self.skill is not None:
            return self.skill

    @property
    def solid(self) -> Union[bool, None]:
        if self.is_specified('part_Physics_Solid'):
            if self.part_Physics_Solid == 'true' or self.part_Physics_Solid == 'True':
                return True
            # add some if-exclusions for things that shouldn't say 'can be walked over/through':
            if self.inheritingfrom == 'Door':  # security doors
                return None
            if self.part_ThrownWeapon is not None:
                # thrown weapons for some reason often specify Solid="false"
                if 'Boulder' not in self.name:
                    return None
            return False

    @property
    def spectacles(self) -> Union[bool, None]:
        """If the item corrects vision."""
        return True if self.part_Spectacles is not None else None

    @property
    def strength(self) -> Union[str, None]:
        """The strength the mutation affects, or the strength of the creature."""
        return self.attribute_helper('Strength')

    @property
    def strengthmult(self) -> Union[float, None]:
        """The stat Bonus multiplier for intrinsic strength, if specified."""
        return self.attribute_boost_factor('Strength')

    @property
    def strengthextrinsic(self) -> Union[int, None]:
        """Extra strength for a creature from extrinsic factors, such as mutations or equipment."""
        if self.active_or_inactive_character() == ACTIVE_CHAR:
            if self.mutation:
                val = 0
                for mutation, info in self.mutation.items():
                    if mutation == 'HeightenedStrength':
                        val += (int(info['Level']) - 1) // 2 + 2
                    if mutation == 'SlogGlands':
                        val += 6
                if val != 0:
                    return val

    @property
    def swarmbonus(self) -> Union[int, None]:
        """The additional bonus that Swarmers receive."""
        return int_or_none(self.part_Swarmer_ExtraBonus)

    @property
    def temponenter(self) -> Union[str, None]:
        """Temperature change caused to objects when weapon/projectile passes through cell.

        Can be a dice string."""
        var = self.projectile_object('part_TemperatureOnEntering_Amount')  # projectiles
        return var or self.part_TemperatureOnEntering_Amount  # melee weapons, etc.

    @property
    def temponhit(self) -> Union[str, None]:
        """Temperature change caused by weapon/projectile hit.

        Can be a dice string."""
        var = self.projectile_object('part_TemperatureOnHit_Amount')
        return var or self.part_TemperatureOnHit_Amount

    @property
    def temponhitmax(self) -> Union[int, None]:
        """Temperature change effect does not occur if target has already reached MaxTemp."""
        temp = self.projectile_object('part_TemperatureOnHit_MaxTemp')
        if temp is not None:
            return int(temp)
        temp = self.part_TemperatureOnHit_MaxTemp
        if temp is not None:
            return int(temp)

    @property
    def thirst(self) -> Union[int, None]:
        """How much thirst it slakes."""
        return int_or_none(self.part_Food_Thirst)

    @property
    def tier(self) -> Union[int, None]:
        """Returns tier. Returns the Specified tier if it isn't inherited. Else it will return
        the highest value bit (if tinkerable) or its FLOOR(Level/5), if neither of these exist,
        it will return the inherited tier value."""
        if not self.is_specified('tag_Tier_Value'):
            if self.is_specified('part_TinkerItem_Bits'):
                val = self.part_TinkerItem_Bits[-1]
                if val.isdigit():
                    return int(val)
                else:
                    return 0
            elif self.lv is not None:
                try:
                    level = int(self.lv)
                except ValueError:
                    # levels can be very rarely given like "18-29"
                    level = int(self.lv.split('-')[0])
                return level // 5
        return int_or_none(self.tag_Tier_Value)

    @property
    def tilecolors(self) -> Union[str, None]:
        """The primary color and detail color used by this object's main image"""
        tile = self.tile
        if tile is not None and tile.tilecolor_letter is not None:
            val = tile.tilecolor_letter
            val += tile.detailcolor_letter if tile.detailcolor_letter is not None else ''
            return val

    @property
    def title(self) -> Union[str, None]:
        """The display name of the item."""
        val = self.name
        if self.builder_GoatfolkHero1_ForceName:
            val = self.builder_GoatfolkHero1_ForceName  # for Mamon
        elif self.name == "Wraith-Knight Templar":
            val = "&MWraith-Knight Templar of the Binary Honorum"  # override for Wraith Knights
        elif self.name == 'TreeSkillsoft':
            val = '&YSkillsoft Plus'  # override for Skillsoft Plus
        elif self.name == 'SingleSkillsoft1':
            val = '&YSkillsoft [&Wlow sp&Y]'  # override for Skillsoft [0-50sp]
        elif self.name == 'SingleSkillsoft2':
            val = '&YSkillsoft [&Wmedium sp&Y]'  # override for Skillsoft [51-150]
        elif self.name == 'SingleSkillsoft3':
            val = '&YSkillsoft [&Whigh sp&Y]'  # override for Skillsoft [151+]
        elif self.name == 'Schemasoft2':
            val = '&YSchemasoft [&Wlow-tier&Y]'  # override for Schemasoft [low-tier]
        elif self.name == 'Schemasoft3':
            val = '&YSchemasoft [&Wmid-tier&Y]'  # override for Schemasoft [mid-tier]
        elif self.name == 'Schemasoft4':
            val = '&YSchemasoft [&Whigh-tier&Y]'  # override for Schemasoft [high-tier]
        elif self.part_Render_DisplayName:
            val = self.part_Render_DisplayName
        if self.part_Roboticized and self.part_Roboticized_ChanceOneIn == '1':
            name_prefix = self.part_Roboticized_NamePrefix
            name_prefix = '{{c|mechanical}}' if not name_prefix else name_prefix
            val = f'{name_prefix} {val}'
        return val

    @property
    def tohit(self) -> Union[int, None]:
        """The bonus or penalty to hit."""
        if self.inherits_from('Armor'):
            return int_or_none(self.part_Armor_ToHit)
        if self.is_specified('part_MeleeWeapon'):
            return int_or_none(self.part_MeleeWeapon_HitBonus)

    @property
    def toughness(self) -> Union[str, None]:
        """The toughness the mutation affects, or the toughness of the creature."""
        return self.attribute_helper('Toughness')

    @property
    def toughnessmult(self) -> Union[float, None]:
        """The stat Bonus multiplier for intrinsic toughness, if specified."""
        return self.attribute_boost_factor('Toughness')

    @property
    def toughnessextrinsic(self) -> Union[int, None]:
        """Extra toughness for a creature from extrinsic factors, such as mutations or equipment."""
        if self.active_or_inactive_character() == ACTIVE_CHAR:
            if self.mutation:
                for mutation, info in self.mutation.items():
                    if mutation == 'HeightenedToughness':
                        return (int(info['Level']) - 1) // 2 + 2

    @property
    def twohanded(self) -> Union[bool, None]:
        """Whether this is a two-handed item."""
        if self.inherits_from('MeleeWeapon') or self.inherits_from('MissileWeapon'):
            if self.tag_UsesSlots and self.tag_UsesSlots != 'Hand':
                return None  # exclude things like Slugsnout Snout
            if self.part_Physics_bUsesTwoSlots or self.part_Physics_UsesTwoSlots:
                return True
            return False

    @property
    def unknowntile(self) -> Union[str, None]:
        """The filename of the object's 'unidentified' tile variant."""
        meta = self.unidentified_metadata()
        if meta is not None:
            return meta.filename

    @property
    def unknownname(self) -> Union[str, None]:
        """The name of the object when unidentified, such as 'weird artifact'."""
        complexity = self.complexity
        if complexity is not None and complexity > 0:
            understanding = self.part_Examiner_Understanding
            if understanding is None or int(understanding) < complexity:
                unknown_name = self.part_Examiner_UnknownDisplayName
                unknown_name = 'weird artifact' if unknown_name is None else unknown_name
                if unknown_name != '*med':
                    return unknown_name

    @property
    def unknownaltname(self) -> Union[str, None]:
        """The name of the object when partially identified, such as 'backpack'."""
        complexity = self.complexity
        if complexity is not None and complexity > 0:
            understanding = self.part_Examiner_Understanding
            if understanding is None or int(understanding) < complexity:
                alt_name = self.part_Examiner_AlternateDisplayName
                alt_name = 'device' if alt_name is None else alt_name
                if alt_name != '*med':
                    return alt_name

    @property
    def unpowereddamage(self) -> Union[str, None]:
        """For weapons that use charge, the damage dealt when unpowered.

        Given as a dice string."""
        return self.part_Gaslight_UnchargedDamage

    @property
    def usesslots(self) -> Union[List[str], None]:
        """Return the body slots taken up by equipping this item.

        This is not the same as the slot the item is equipped "to", which is given by wornon

        Example: Portable Beehive returns ["Back", "Floating Nearby"].
        """
        if self.tag_UsesSlots_Value is not None:
            return self.tag_UsesSlots_Value.split(',')

    @property
    def vibro(self) -> Union[bool, None]:
        """Whether this is a vibro weapon."""
        # if self.is_specified('part_ThrownWeapon'):
        if self.is_specified('part_GeomagneticDisc'):
            return True
        elif self.is_specified('part_MissileWeapon'):
            attributes = self.projectile_object('part_Projectile_Attributes')
            if attributes is not None:
                if 'Vorpal' in attributes.split(' '):
                    return True
        elif self.inherits_from('MeleeWeapon') or self.inherits_from('NaturalWeapon'):
            if self.part_VibroWeapon:
                return True

    @property
    def waterritualable(self) -> Union[bool, None]:
        """Whether the creature is waterritualable."""
        if self.is_specified('xtag_WaterRitual') or self.part_GivesRep is not None:
            return True

    @property
    def waterritualskill(self) -> Union[str, None]:
        """What skill that individual teaches, if they have any."""
        if self.is_specified('xtag_WaterRitual') or self.part_GivesRep is not None:
            return self.xtag_WaterRitual_SellSkill

    @property
    def weaponskill(self) -> Union[str, None]:
        """The skill tree required for use."""
        val = None
        if self.inherits_from('MeleeWeapon') or self.is_specified('part_MeleeWeapon'):
            val = self.part_MeleeWeapon_Skill
        if self.inherits_from('MissileWeapon'):
            if self.part_MissileWeapon_Skill is not None:
                val = self.part_MissileWeapon_Skill
        if self.part_Gaslight:
            val = self.part_Gaslight_ChargedSkill
        # disqualify various things from showing the 'cudgel' skill:
        if self.inherits_from('Projectile'):
            val = None
        if self.inherits_from('Shield'):
            val = 'Shield'
        return val

    @property
    def weight(self) -> Union[int, None]:
        """The weight of the object."""
        if self.inherits_from('InertObject') or self.inherits_from('CosmeticObject') or \
                (self.part_Physics_IsReal is not None and self.part_Physics_IsReal == 'false') or \
                self.tag_IgnoresGravity is not None or \
                self.tag_ExcavatoryTerrainFeature is not None:
            return None
        return int_or_none(self.part_Physics_Weight)

    @property
    def willpower(self) -> Union[str, None]:
        """The willpower the mutation affects, or the willpower of the creature."""
        return self.attribute_helper('Willpower')

    @property
    def willpowermult(self) -> Union[float, None]:
        """The stat Bonus multiplier for intrinsic willpower, if specified."""
        return self.attribute_boost_factor('Willpower')

    @property
    def willpowerextrinsic(self) -> Union[int, None]:
        """Extra willpower for a creature from extrinsic factors, such as mutations or equipment."""
        return None  # nothing currently supported here

    @property
    def wornon(self) -> Union[str, None]:
        """The body slot that an item gets equipped to.

        Not the same as the body slots it occupies once equipped, which is given by usesslots."""
        wornon = None
        if self.part_Shield_WornOn:
            wornon = self.part_Shield_WornOn
        if self.part_Armor_WornOn:
            wornon = self.part_Armor_WornOn
        if self.name == 'Hooks':
            wornon = 'Feet'  # manual fix
        return wornon

    @property
    def xpvalue(self) -> Union[int, None]:
        level = self.lv
        try:
            # there's one object that uses an sValue for "Level" ('Barathrumite Tinker' => '18-29')
            # that object and its children are not wiki-enabled, but would raise an exception here.
            level = int_or_none(level)
        except ValueError:
            level = None
        if level is None:
            return None
        xp_value = getattr(self, 'stat_XPValue_sValue')
        if not xp_value:
            xp_value = getattr(self, 'stat_XPValue_Value')
        if not xp_value:
            return None
        xp = xp_value
        if xp == '*XP':
            role = self.role
            role = 'Minion' if role is None else role
            if role == 'Minion':
                xp = level * 10
            elif role == 'Leader':
                xp = level * 50
            elif role == 'Hero':
                xp = level * 100
            else:
                xp = level * 25
        else:
            xp = int_or_none(xp)
            if xp is None:
                return None
        return xp

    @property
    def xptier(self) -> Union[int, None]:
        level = self.lv
        try:
            # there's one object that uses an sValue for "Level" ('Barathrumite Tinker' => '18-29')
            # that object and its children are not wiki-enabled, but would raise an exception here.
            level = int_or_none(level)
        except ValueError:
            return None
        if level is not None:
            return level // 5
