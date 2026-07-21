import BellKAT.QuantumPrelude hiding (lookup)
import qualified Common.NondetTopology as Nondet
import qualified Common.NetworkConfig as Net
import Data.List (stripPrefix)
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

protocol :: Scenario -> QBKATPolicy
protocol scenario =
    Nondet.leftToRightProtocol (selectedLoopGuard scenario)

selectedLoopGuard :: Scenario -> QBKATTest
selectedLoopGuard scenario
    | scAdaptLoopTest scenario =
        either error id (Nondet.selectLoopTest (scEventName scenario))
    | otherwise =
        Nondet.missingAnyGoal

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

nb :: NetworkBounds QBKATTag
nb = Nondet.protocolBounds Nondet.LeftToRight

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
    Nondet.actionConfigFor (networkParameters scenario)

main :: IO ()
main = do
    args <- getArgs
    (scenario, qbkatArgs) <-
        either fail pure (stripExampleArgs args)
    ev <- either fail pure (Nondet.selectEvent (scEventName scenario))
    withArgs qbkatArgs $
        qbkatMainD (actionConfig scenario) nb ev (protocol scenario) mempty
