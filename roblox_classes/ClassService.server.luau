--!strict
-- Script: ServerScriptService.ClassService
-- Нужно: ReplicatedStorage.ClassConfig (ModuleScript) и ReplicatedStorage.Remotes (Folder).
-- Клиент шлёт только намерение «крутить», результат решает сервер.

local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local DataStoreService = game:GetService("DataStoreService")

local ClassConfig = require(ReplicatedStorage:WaitForChild("ClassConfig"))

local STORE = DataStoreService:GetDataStore("PlayerClass_v1")
local SCHEMA_VERSION = 1
local SPIN_COOLDOWN = 0.5

local remotes = ReplicatedStorage:FindFirstChild("Remotes")
if not remotes then
	remotes = Instance.new("Folder")
	remotes.Name = "Remotes"
	remotes.Parent = ReplicatedStorage
end

local spinRemote = Instance.new("RemoteFunction")
spinRemote.Name = "SpinClass"
spinRemote.Parent = remotes

local rng = Random.new()

type Session = { ClassId: string, Loaded: boolean, Busy: boolean, LastSpin: number, Dirty: boolean }
local sessions: { [Player]: Session } = {}

-- ПОДКЛЮЧИ К СВОЕЙ ВАЛЮТЕ: по умолчанию ищет leaderstats.Gems (IntValue/NumberValue).
local function spendGems(player: Player, amount: number): boolean
	local stats = player:FindFirstChild("leaderstats")
	local gems = stats and stats:FindFirstChild("Gems")
	if not gems or not (gems:IsA("IntValue") or gems:IsA("NumberValue")) then
		warn("[ClassService] не найден leaderstats.Gems, подключи spendGems к своей валюте")
		return false
	end
	local value = gems :: IntValue
	if value.Value < amount then
		return false
	end
	value.Value -= amount
	return true
end

local function refundGems(player: Player, amount: number)
	local stats = player:FindFirstChild("leaderstats")
	local gems = stats and stats:FindFirstChild("Gems")
	if gems and (gems:IsA("IntValue") or gems:IsA("NumberValue")) then
		(gems :: IntValue).Value += amount
	end
end

-- Баффы доступны остальным скриптам через атрибуты игрока: Buff_SellMult, Buff_BagMult, ...
local function applyClass(player: Player, classId: string)
	local buffs = ClassConfig.GetBuffs(classId)
	player:SetAttribute("ClassId", classId)
	for key, value in buffs do
		player:SetAttribute("Buff_" .. key, value)
	end
	local def = ClassConfig.Get(classId)
	player:SetAttribute("ClassSpecial", def and def.Special or "")
end

local function withRetry<T>(fn: () -> T): (boolean, T?)
	for attempt = 1, 4 do
		local ok, result = pcall(fn)
		if ok then
			return true, result
		end
		warn(("[ClassService] ошибка DataStore (попытка %d): %s"):format(attempt, tostring(result)))
		task.wait(2 ^ attempt)
	end
	return false, nil
end

local function load(player: Player)
	local key = "u_" .. player.UserId
	local ok, data = withRetry(function()
		return STORE:GetAsync(key)
	end)
	if not player.Parent then
		return
	end
	if not ok then
		-- Не затираем реальные данные дефолтом: сессия помечена как незагруженная, сохранять её нельзя
		player:Kick("Не удалось загрузить данные. Зайди ещё раз.")
		return
	end
	local classId = ClassConfig.DefaultClass
	if type(data) == "table" and type(data.Class) == "string" and ClassConfig.Get(data.Class) then
		classId = data.Class
	end
	sessions[player] = { ClassId = classId, Loaded = true, Busy = false, LastSpin = 0, Dirty = false }
	applyClass(player, classId)
end

local function save(player: Player)
	local session = sessions[player]
	if not session or not session.Loaded or not session.Dirty then
		return
	end
	local key = "u_" .. player.UserId
	local classId = session.ClassId
	local ok = withRetry(function()
		STORE:UpdateAsync(key, function()
			return { Version = SCHEMA_VERSION, Class = classId }
		end)
		return true
	end)
	if ok then
		session.Dirty = false
	end
end

local function rollClass(): string
	local roll = rng:NextNumber() * 100
	local acc = 0
	for _, def in ClassConfig.Classes do
		acc += def.Chance
		if roll < acc then
			return def.Id
		end
	end
	return ClassConfig.Classes[#ClassConfig.Classes].Id -- запас на погрешность float
end

spinRemote.OnServerInvoke = function(player: Player)
	local session = sessions[player]
	if not session or not session.Loaded or session.Busy then
		return { ok = false, reason = "busy" }
	end
	if os.clock() - session.LastSpin < SPIN_COOLDOWN then
		return { ok = false, reason = "cooldown" }
	end
	session.Busy = true
	session.LastSpin = os.clock()

	if not spendGems(player, ClassConfig.RollCost) then
		session.Busy = false
		return { ok = false, reason = "no_gems" }
	end

	local newClass = rollClass()
	session.ClassId = newClass
	session.Dirty = true
	applyClass(player, newClass)
	session.Busy = false

	task.spawn(save, player)
	return { ok = true, classId = newClass }
end

-- Особые способности: здесь только заглушки, реальные эффекты подключи к своей логике снега.
-- Золотой снег (WinterLord): раз в 60–120 сек пометь снег вокруг игрока как золотой.
-- Заморозка (AbsoluteZero): раз в 25 сек мгновенно собери снег в радиусе вокруг игрока.
local specials: { [string]: (Player) -> () } = {
	GoldenSnow = function(player: Player)
		player:SetAttribute("GoldenSnowActive", true)
		task.delay(15, function()
			if player.Parent then
				player:SetAttribute("GoldenSnowActive", false)
			end
		end)
	end,
	FreezePulse = function(player: Player)
		player:SetAttribute("FreezePulseAt", os.clock()) -- слушай этот атрибут в логике сбора снега
	end,
}

task.spawn(function()
	local nextAt: { [Player]: number } = {}
	while true do
		task.wait(1)
		local now = os.clock()
		for _, player in Players:GetPlayers() do
			local id = player:GetAttribute("ClassSpecial")
			local fn = type(id) == "string" and specials[id]
			if fn then
				local due = nextAt[player]
				if not due then
					due = now + (id == "FreezePulse" and 25 or rng:NextInteger(60, 120))
					nextAt[player] = due
				end
				if now >= due then
					fn(player)
					nextAt[player] = nil
				end
			else
				nextAt[player] = nil
			end
		end
	end
end)

Players.PlayerAdded:Connect(function(player)
	task.spawn(load, player)
end)
for _, player in Players:GetPlayers() do
	task.spawn(load, player)
end

Players.PlayerRemoving:Connect(function(player)
	save(player)
	sessions[player] = nil
end)

game:BindToClose(function()
	local pending = 0
	for _, player in Players:GetPlayers() do
		pending += 1
		task.spawn(function()
			save(player)
			pending -= 1
		end)
	end
	while pending > 0 do
		task.wait()
	end
end)
