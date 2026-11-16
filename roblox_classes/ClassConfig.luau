--!strict
-- ModuleScript: ReplicatedStorage.ClassConfig
-- Шансы (Chance) в процентах, сумма = 100. Чем ниже в списке, тем реже и сильнее.

export type Buffs = {
	SellMult: number?, -- +доля к цене продажи снега (0.2 = +20%)
	BagMult: number?, -- +доля к вместимости сумки
	RobotSpeed: number?, -- +доля к скорости роботов
	RobotYield: number?, -- +доля к количеству снега, которое собирают роботы
	DigSpeed: number?, -- +доля к скорости копания
	DiamondChance: number?, -- +доля к шансу найти алмаз в снегу
	UpgradeDiscount: number?, -- скидка на апгрейды (0.2 = -20%)
	ExtraRobots: number?, -- плюс роботов со старта, не в счёт базовых трёх
}

export type ClassDef = {
	Id: string,
	Name: string,
	Chance: number,
	Buffs: Buffs,
	Desc: string,
	Special: string?, -- id особой способности (реализуется отдельно в ClassService)
	SpecialDesc: string?,
}

local ClassConfig = {}

ClassConfig.RollCost = 40 -- алмазов за один прокрут
ClassConfig.MaxUpgradeDiscount = 0.5 -- потолок суммарной скидки, чтобы цена не уходила в ноль
ClassConfig.DefaultClass = "Starter"

ClassConfig.Classes = {
	{ Id = "Starter", Name = "Новичок", Chance = 35.5,
		Buffs = { SellMult = 0.05 },
		Desc = "Снег продаётся на 5% дороже." },
	{ Id = "Porter", Name = "Грузчик", Chance = 22,
		Buffs = { BagMult = 0.15 },
		Desc = "Сумка вмещает на 15% больше." },
	{ Id = "SnowTrader", Name = "Снеготорговец", Chance = 12.5,
		Buffs = { SellMult = 0.12 },
		Desc = "Снег продаётся на 12% дороже." },
	{ Id = "Digger", Name = "Копатель", Chance = 8,
		Buffs = { DigSpeed = 0.15 },
		Desc = "Копаете на 15% быстрее." },
	{ Id = "Mechanic", Name = "Механик", Chance = 7,
		Buffs = { RobotSpeed = 0.12 },
		Desc = "Роботы ездят на 12% быстрее." },
	{ Id = "DiamondHunter", Name = "Кладоискатель", Chance = 6.5,
		Buffs = { DiamondChance = 0.10 },
		Desc = "Шанс найти алмаз в снегу выше на 10%." },
	{ Id = "Banker", Name = "Банкир", Chance = 4.5,
		Buffs = { SellMult = 0.20, BagMult = 0.10 },
		Desc = "Снег продаётся на 20% дороже, а сумка вмещает на 10% больше." },
	{ Id = "Engineer", Name = "Инженер", Chance = 2.5,
		Buffs = { ExtraRobots = 1, RobotSpeed = 0.10 },
		Desc = "+1 робот со старта сверх базовых трёх. Роботы ездят на 10% быстрее." },
	{ Id = "SnowTycoon", Name = "Снежный магнат", Chance = 1.2,
		Buffs = { SellMult = 0.30, UpgradeDiscount = 0.20, BagMult = 0.20 },
		Desc = "Снег дороже на 30%, апгрейды дешевле на 20%, сумка больше на 20%." },
	{ Id = "WinterLord", Name = "Повелитель зимы", Chance = 0.25,
		Buffs = { RobotYield = 0.40, DigSpeed = 0.25, SellMult = 0.20 },
		Desc = "Роботы собирают на 40% больше, копание быстрее на 25%, снег дороже на 20%.",
		Special = "GoldenSnow", SpecialDesc = "Случайным образом активирует золотой снег" },
	{ Id = "AbsoluteZero", Name = "Абсолютный ноль", Chance = 0.05,
		Buffs = { SellMult = 0.40, UpgradeDiscount = 0.30, RobotYield = 0.35, ExtraRobots = 1 },
		Desc = "Все классы одновременно, но слабее. +1 робот со старта.",
		Special = "FreezePulse", SpecialDesc = "Каждые 25 секунд замораживает и мгновенно собирает снег вокруг" },
} :: { ClassDef }

local byId: { [string]: ClassDef } = {}
for _, def in ClassConfig.Classes do
	byId[def.Id] = def
end

function ClassConfig.Get(id: string): ClassDef?
	return byId[id]
end

-- Суммарные баффы класса в виде плоской таблицы (отсутствующие = 0)
function ClassConfig.GetBuffs(id: string): { [string]: number }
	local def = byId[id] or byId[ClassConfig.DefaultClass]
	local out = {
		SellMult = 0, BagMult = 0, RobotSpeed = 0, RobotYield = 0,
		DigSpeed = 0, DiamondChance = 0, UpgradeDiscount = 0, ExtraRobots = 0,
	}
	for key, value in def.Buffs :: any do
		out[key] = value
	end
	out.UpgradeDiscount = math.min(out.UpgradeDiscount, ClassConfig.MaxUpgradeDiscount)
	return out
end

-- Проверка при старте: сумма шансов должна быть 100
do
	local total = 0
	for _, def in ClassConfig.Classes do
		total += def.Chance
	end
	if math.abs(total - 100) > 0.001 then
		warn(("[ClassConfig] сумма шансов = %.3f, а должна быть 100"):format(total))
	end
end

return ClassConfig
