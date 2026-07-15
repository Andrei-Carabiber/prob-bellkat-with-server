import BellKAT.QuantumPrelude hiding (lookup)
import qualified Common.NetworkConfig as Net
import Data.List (intercalate, stripPrefix)
import System.Environment (getArgs, withArgs)
import Text.Read (readMaybe)

data Scenario = Scenario
    { scEventName :: String
    , scAdaptLoopTest :: Bool
    , scPGenOverride :: Maybe Double
    , scPSwapOverride :: Maybe Double
    , scW0Override :: Maybe Double
    , scTCohOverride :: Maybe Int
    }

defaultScenario :: Scenario
defaultScenario = Scenario
    { scEventName = "a-c"
    , scAdaptLoopTest = False
    , scPGenOverride = Nothing
    , scPSwapOverride = Nothing
    , scW0Override = Nothing
    , scTCohOverride = Nothing
    }

missingAnyGoal :: QBKATTest
missingAnyGoal =
        "A" /~? "C"
    &&* "B" /~? "D"

leftAGuard :: QBKATTest
leftAGuard = hasSubset ["A" ~ "X", "X" ~ "Y"] &&* "A" /~? "Y" &&* "A" /~? "C"

leftBGuard :: QBKATTest
leftBGuard = hasSubset ["B" ~ "X", "X" ~ "Y"] &&* "B" /~? "Y" &&* "B" /~? "D"

goalACGuard :: QBKATTest
goalACGuard = hasSubset ["A" ~ "Y", "C" ~ "Y"] &&* "A" /~? "C"

goalBDGuard :: QBKATTest
goalBDGuard = hasSubset ["B" ~ "Y", "D" ~ "Y"] &&* "B" /~? "D"

protocol :: Scenario -> QBKATPolicy
protocol scenario =
    while loopGuard loopBody
  where
    loopGuard = selectedLoopGuard scenario
    loopBody =
        generations
        <>
        chooseLeftBranch
        <>
        chooseRightEndpoint

generations :: QBKATPolicy
generations =
        ucreate ("A", "X")
    <||>
        ucreate ("B", "X")
    <||>
        ucreate ("X", "Y")
    <||>
        ucreate ("C", "Y")
    <||>
        ucreate ("D", "Y")

chooseLeftBranch :: QBKATPolicy
chooseLeftBranch =
        ite leftAGuard
            (swap "X" ("A", "Y"))
            mempty
    <||>
        ite leftBGuard
            (swap "X" ("B", "Y"))
            mempty

chooseRightEndpoint :: QBKATPolicy
chooseRightEndpoint =
        ite goalACGuard
            (swap "Y" ("A", "C"))
            mempty
    <||>
        ite goalBDGuard
            (swap "Y" ("B", "D"))
            mempty

events :: [(String, QBKATTest)]
events =
    [ ("a-c", "A" ~~? "C")
    , ("b-d", "B" ~~? "D")
    , ("either", "A" ~~? "C" ||* "B" ~~? "D")
    , ("a-c-or-b-d", "A" ~~? "C" ||* "B" ~~? "D")
    ]

loopTests :: [(String, QBKATTest)]
loopTests =
    [ ("a-c", "A" /~? "C")
    , ("b-d", "B" /~? "D")
    , ("either", missingAnyGoal)
    , ("a-c-or-b-d", missingAnyGoal)
    ]

selectedLoopGuard :: Scenario -> QBKATTest
selectedLoopGuard scenario
    | scAdaptLoopTest scenario =
        maybe
            (error $ "missing loop test for event " <> show (scEventName scenario))
            id
            (lookup (scEventName scenario) loopTests)
    | otherwise =
        missingAnyGoal

selectEvent :: String -> Either String QBKATTest
selectEvent name =
    maybe
        (Left $ "Unknown event '" <> name <> "'. Available events: " <> availableEvents)
        Right
        (lookup name events)

availableEvents :: String
availableEvents = intercalate ", " (fmap fst events)

readFlag :: Read a => String -> String -> Either String a
readFlag flag raw =
    case readMaybe raw of
        Nothing -> Left $ "Could not parse " <> flag <> " value '" <> raw <> "'."
        Just value -> Right value

setDouble :: String -> (Double -> Scenario -> Scenario) -> String -> Scenario -> Either String Scenario
setDouble flag setter raw scenario =
    fmap (`setter` scenario) (readFlag flag raw)

setInt :: String -> (Int -> Scenario -> Scenario) -> String -> Scenario -> Either String Scenario
setInt flag setter raw scenario =
    fmap (`setter` scenario) (readFlag flag raw)

stripExampleArgs :: [String] -> Either String (Scenario, [String])
stripExampleArgs = go defaultScenario []
  where
    go scenario kept [] =
        validateScenario scenario *> Right (scenario, reverse kept)
    go _ _ ["--event"] = Left "Missing value for --event."
    go _ _ ["--p-gen-override"] = Left "Missing value for --p-gen-override."
    go _ _ ["--p-swap"] = Left "Missing value for --p-swap."
    go _ _ ["--w0-override"] = Left "Missing value for --w0-override."
    go _ _ ["--t-coh"] = Left "Missing value for --t-coh."
    go scenario kept ("--event" : name : rest) =
        go scenario{scEventName = name} kept rest
    go scenario kept ("--adapt-loop-test" : rest) =
        go scenario{scAdaptLoopTest = True} kept rest
    go scenario kept ("--p-gen-override" : raw : rest) =
        setDouble "--p-gen-override" (\value sc -> sc{scPGenOverride = Just value}) raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--p-swap" : raw : rest) =
        setDouble "--p-swap" (\value sc -> sc{scPSwapOverride = Just value}) raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--w0-override" : raw : rest) =
        setDouble "--w0-override" (\value sc -> sc{scW0Override = Just value}) raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept ("--t-coh" : raw : rest) =
        setInt "--t-coh" (\value sc -> sc{scTCohOverride = Just value}) raw scenario >>= \updated ->
            go updated kept rest
    go scenario kept (arg : rest)
        | Just name <- stripPrefix "--event=" arg =
            go scenario{scEventName = name} kept rest
        | arg == "--adapt-loop-test" =
            go scenario{scAdaptLoopTest = True} kept rest
        | Just raw <- stripPrefix "--p-gen-override=" arg =
            setDouble "--p-gen-override" (\value sc -> sc{scPGenOverride = Just value}) raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--p-swap=" arg =
            setDouble "--p-swap" (\value sc -> sc{scPSwapOverride = Just value}) raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--w0-override=" arg =
            setDouble "--w0-override" (\value sc -> sc{scW0Override = Just value}) raw scenario >>= \updated ->
                go updated kept rest
        | Just raw <- stripPrefix "--t-coh=" arg =
            setInt "--t-coh" (\value sc -> sc{scTCohOverride = Just value}) raw scenario >>= \updated ->
                go updated kept rest
        | otherwise =
            go scenario (arg : kept) rest

validateScenario :: Scenario -> Either String ()
validateScenario scenario
    | maybe False invalidProbability (scPGenOverride scenario) =
        Left "--p-gen-override must be a 50 km reference probability in the interval [0, 1]."
    | maybe False invalidProbability (scPSwapOverride scenario) =
        Left "--p-swap must be in the interval [0, 1]."
    | maybe False invalidProbability (scW0Override scenario) =
        Left "--w0-override must be a 50 km reference Werner parameter in the interval [0, 1]."
    | maybe False (<= 0) (scTCohOverride scenario) =
        Left "--t-coh must be positive."
    | otherwise =
        Right ()
  where
    invalidProbability value = value < 0 || value > 1

capacityPairs :: [(Location, Location)]
capacityPairs =
    generationLinks <>
    [ ("A", "Y")
    , ("B", "Y")
    , ("A", "C")
    , ("B", "D")
    ]

nb :: NetworkBounds QBKATTag
nb = Net.networkBoundsFor capacityPairs

generationLinks :: [(Location, Location)]
generationLinks =
    [ ("A", "X")
    , ("B", "X")
    , ("X", "Y")
    , ("C", "Y")
    , ("D", "Y")
    ]

networkParameters :: Scenario -> Net.NetworkParameters
networkParameters scenario =
    applyTCohOverride
    . applyPSwapOverride
    . applyW0Override
    . applyPGenOverride
    $ Net.defaultNetworkParameters
  where
    applyPGenOverride parameters =
        case scPGenOverride scenario of
            Nothing -> parameters
            Just value -> parameters{Net.npReferencePGen = value}
    applyW0Override parameters =
        case scW0Override scenario of
            Nothing -> parameters
            Just value -> parameters{Net.npReferenceW0 = value}
    applyPSwapOverride parameters =
        case scPSwapOverride scenario of
            Nothing -> parameters
            Just value -> Net.withUniformSwapProbability value parameters
    applyTCohOverride parameters =
        case scTCohOverride scenario of
            Nothing -> parameters
            Just value -> Net.withUniformCoherenceTime value parameters

actionConfig :: Scenario -> ProbabilisticActionConfiguration
actionConfig scenario =
    Net.actionConfigFor (networkParameters scenario) generationLinks ["X", "Y"]

main :: IO ()
main = do
    args <- getArgs
    (scenario, qbkatArgs) <-
        either fail pure (stripExampleArgs args)
    ev <- either fail pure (selectEvent (scEventName scenario))
    withArgs qbkatArgs $
        qbkatMainD (actionConfig scenario) nb ev (protocol scenario) mempty
